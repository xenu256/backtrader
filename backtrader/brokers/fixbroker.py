#!/usr/bin/env python
# -*- coding: utf-8; py-indent-offset:4 -*-
###############################################################################
#
# Copyright (C) 2015, 2016, 2017 Daniel Rodriguez
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################

from __future__ import (absolute_import, division, print_function)

from collections import defaultdict
from datetime import datetime
from time import sleep
import threading

from backtrader import BrokerBase, OrderBase, Order
from backtrader.utils.py3 import queue

import quickfix as fix

class FIXOrder(OrderBase):
    # Map backtrader order types to the FIX ones
    _OrderTypes = {
        None: fix.OrdType_MARKET,  # default
        Order.Market: fix.OrdType_MARKET,
        Order.Limit: fix.OrdType_LIMIT,
        Order.Close: fix.OrdType_ON_CLOSE,
        Order.Stop: fix.OrdType_STOP,
        Order.StopLimit: fix.OrdType_STOP_LIMIT,
        #Order.StopTrail: ???,
        #Order.StopTrailLimit: ???,
    }

    def __init__(self, owner, data, exectype, side, amount, price, params, settings):
        self.exectype = exectype
        self.ordtype = self.Buy if side == 'buy' else self.Sell
        self.data = data
        self.settings = settings
        OrderBase.__init__(self)

        now = datetime.utcnow()
        #self.order_id = now.strftime("%Y-%m-%d_%H:%M:%S_%f-%%s") % now.microsecond
        self.order_id = now.strftime("%Y-%m-%d_%H:%M:%S_%f")

        msg = fix.Message()
        msg.getHeader().setField(fix.BeginString(fix.BeginString_FIX42))
        msg.getHeader().setField(fix.MsgType(fix.MsgType_NewOrderSingle)) #39=D
        msg.setField(fix.ClOrdID(self.order_id)) #11=Unique order
        msg.setField(fix.OrderID(self.order_id)) # 37
        msg.setField(fix.HandlInst(fix.HandlInst_MANUAL_ORDER_BEST_EXECUTION)) #21=3 (Manual order, best executiona)
        msg.setField(fix.Symbol(data._name)) #55
        msg.setField(fix.Side(fix.Side_BUY if side == 'buy' else fix.Side_SELL)) #43
        msg.setField(fix.OrdType(self._OrderTypes[exectype])) #40=2 Limit order
        msg.setField(fix.OrderQty(amount)) #38=100
        msg.setField(fix.OrderQty2(amount))
        msg.setField(fix.Price(price))
        msg.setField(fix.TransactTime())

        sdict = settings.get()
        msg.setField(fix.ExDestination(sdict.getString("Destination")))
        msg.setField(fix.Account(sdict.getString("Account")))
        msg.setField(fix.TargetSubID(sdict.getString("TargetSubID")))

        self.msg = msg
        print("DEBUG: order msg:", msg)

    def submit(self, app):
        fix.Session.sendToTarget(self.msg, app.sessionID)

    def cancel(self, app):
        msg = fix.Message();
        header = msg.getHeader();

        header.setField(fix.BeginString(fix.BeginString_FIX42))
        header.setField(fix.MsgType(fix.MsgType_OrderCancelRequest))

        msg.setField(fix.ClOrdID(self.order_id))

        sdict = self.settings.get()
        msg.setField(fix.ExDestination(sdict.getString("Destination")))
        msg.setField(fix.Account(sdict.getString("Account")))
        msg.setField(fix.TargetSubID(sdict.getString("TargetSubID")))

        fix.Session.sendToTarget(msg, app.sessionID)

        #######
        #header.setField(quickfix.SenderCompID(TW))
        #header.setField(quickfix.TargetCompID("TARGET"))
        #message.setField(quickfix.OrigClOrdID("123"))
        #message.setField(quickfix.Symbol("LNUX"))
        #message.setField(quickfix.Side(Side_BUY))
        #message.setField(quickfix.Text("Cancel My Order!"))


class FIXApplication(fix.Application):
    def __init__(self, broker):
        fix.Application.__init__(self)
        self.broker = broker

    def onCreate(self, sessionID):
        print("DEBUG: onCreate:", sessionID)

    def onLogon(self, sessionID):
        self.sessionID = sessionID
        print("DEBUG: onLogon:", sessionID)

    def onLogout(self, sessionID):
        print("DEBUG: onLogout:", sessionID)

    def onMessage(self, message, sessionID):
        print("DEBUG: onMessage: ", sessionID, message.toString().replace('\x01', '|'))

    def toAdmin(self, message, sessionID):
        msgType = fix.MsgType()
        message.getHeader().getField(msgType)
        if msgType.getValue() == fix.MsgType_Logon:
            message.getHeader().setField(fix.TargetSubID("executor"))
        elif msgType.getValue() == fix.MsgType_Heartbeat:
            print("DEBUG: Heartbeat reply")
        else:
            print("DEBUG: toAdmin: ", sessionID, message.toString().replace('\x01', '|'))

    def fromAdmin(self, message, sessionID): #, message):
        msgType = fix.MsgType()
        message.getHeader().getField(msgType)
        if msgType.getValue() == fix.MsgType_Heartbeat:
            print("DEBUG: Heartbeat")
        else:
            print("DEBUG: fromAdmin: ", sessionID, message.toString().replace('\x01', '|'))

    def toApp(self, sessionID, message):
        print("DEBUG: toApp: ", sessionID, message.toString().replace('\x01', '|'))

    def fromApp(self, message, sessionID):
        msgType = fix.MsgType()
        message.getHeader().getField(msgType)
        tag = msgType.getValue()
        if tag == fix.MsgType_News:
            result = []
            for item in message.toString().split('\x01'):
                if not item or not '=' in item:
                    continue
                code, val = item.split("=")
                if code == '10008':
                    result.append([val, None])
                if code == '58':
                    for valtype in int, float, str:
                        try:
                            val = valtype(val)
                        except ValueError:
                            continue
                        break
                    result[-1][1] = val
            for key, value in result:
                if hasattr(self.broker, key):
                    setattr(self.broker, key, value)
            print(result)
        elif tag == fix.MsgType_ExecutionReport:
            print("DEBUG: execution report: ", sessionID, message.toString().replace('\x01', '|'))
            execType = fix.ExecType()
            message.getField(execType)
            etype = execType.getValue()
            if etype in (fix.ExecType_PENDING_NEW, fix.ExecType_NEW):
                order_id_type = fix.ClOrdID()
                message.getField(order_id_type)
                order_id = order_id_type.getValue()
                print("DEBUG: Pending [new]", order_id)
                if order_id in self.broker.orders:
                    self.broker.orders[order_id].status = Order.Accepted
                    self.broker.notify(self.broker.orders[order_id])
            elif etype == fix.ExecType_FILL:
                price_type = fix.Price()
                message.getField(price_type)
                price = price_type.getValue()

                quantity_type = fix.OrderQty()
                message.getField(quantity_type)
                quantity = quantity_type.getValue()

                side_type = fix.Side()
                message.getField(side_type)
                side = side_type.getValue()

                order_id_type = fix.ClOrdID()
                message.getField(order_id_type)
                order_id = order_id_type.getValue()

                symbol_type = fix.Symbol()
                message.getField(symbol_type)
                symbol = symbol_type.getValue()

                print("DEBUG: filled", order_id)

                # Update order
                if order_id in self.broker.orders:
                    self.broker.orders[order_id].status = Order.Completed
                    self.broker.positions[symbol].append((side, quantity, price))
                    self.broker.notify(self.broker.orders[order_id])

            elif etype == fix.ExecType_REJECTED:
                order_id_type = fix.ClOrdID()
                message.getField(order_id_type)
                order_id = order_id_type.getValue()

                print("DEBUG: Rejected", order_id)

        else:
            print("DEBUG: fromApp: ", sessionID, message.toString().replace('\x01', '|'))


class FIXBroker(BrokerBase):
    '''Broker implementation for FIX protocol using quickfix library.'''

    def __init__(self, config, debug=False):
        BrokerBase.__init__(self)

        self.config = config
        self.debug = debug

        self.queue = queue.Queue()  # holds orders which are notified

        self.startingcash = self.cash = 0.0
        self.done = False

        self.app = None

        self.orders = {}
        self.positions = defaultdict(list)

        self.setting = None

        # attributes set by fix.Application
        self.HardBuyingPowerLimit = 0

        # start quickfix main loop in a separate thread
        thread = threading.Thread(target=self.fix)
        thread.start()

    def fix(self):
        self.settings = fix.SessionSettings(self.config)
        sdict = self.settings.get()
        storeFactory = fix.FileStoreFactory(self.settings)
        logFactory = fix.ScreenLogFactory(self.settings)
        self.app = FIXApplication(self)
        initiator = fix.SocketInitiator(self.app, storeFactory, self.settings, logFactory)
        initiator.start()
        while not self.done:
            sleep(1)
        initiator.stop()

    def stop(self):
        self.done = True

    def getcash(self):
        return self.HardBuyingPowerLimit

    def getvalue(self, datas=None):
        return self.HardBuyingPowerLimit

    def getposition(self, data):
        print(">>> getposition",  data)
        return 0

    def get_notification(self):
        try:
            return self.queue.get(False)
        except queue.Empty:
            pass

    def notify(self, order):
        self.queue.put(order)

    def _submit(self, owner, data, exectype, side, amount, price, params):

        order = FIXOrder(owner, data, exectype, side, amount,
                         price, params, self.settings)
        order.submit(self.app)
        self.orders[order.order_id] = order
        self.notify(order)
        return order

    def buy(self, owner, data, size, price=None, plimit=None,
            exectype=None, valid=None, tradeid=0, oco=None,
            trailamount=None, trailpercent=None,
            **kwargs):
        return self._submit(owner, data, exectype, 'buy', size, price, kwargs)

    def sell(self, owner, data, size, price=None, plimit=None,
             exectype=None, valid=None, tradeid=0, oco=None,
             trailamount=None, trailpercent=None,
             **kwargs):
        return self._submit(owner, data, exectype, 'sell', size, price, kwargs)

    def cancel(self, order):
        print("DEBUG: canceling order", order)
        _order = self.orders.get(order.order_id)
        if not ord:
            print("DEBUG: order not found", order)
            return # not found ... not cancellable

        if _order.status == Order.Cancelled:  # already cancelled
            return

        _order.cancel(self.app)
