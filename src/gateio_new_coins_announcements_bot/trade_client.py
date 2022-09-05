import json
from datetime import datetime

from gate_api import ApiClient
from gate_api import Order
from gate_api import SpotApi
from gate_api import MarginApi

from gateio_new_coins_announcements_bot.auth.gateio_auth import load_gateio_creds
from kgateio_new_coins_announcements_bot.logger import logger
from gateio_new_coins_announcements_bot.new_listings_scraper import get_all_cross_margin_pairs_leverage

client = load_gateio_creds("auth/auth.yml")
spot_api = SpotApi(ApiClient(client))
margin_api = MarginApi(ApiClient(client))

last_trade = None


def get_last_price(base, quote, return_price_only):
    """
    Args:
    'DOT', 'USDT'
    """
    global last_trade
    trades = spot_api.list_trades(currency_pair=f"{base}_{quote}", limit=1)
    assert len(trades) == 1
    trade = trades[0]

    create_time_ms = datetime.utcfromtimestamp(int(trade.create_time_ms.split(".")[0]) / 1000)
    create_time_formatted = create_time_ms.strftime("%d-%m-%y %H:%M:%S.%f")

    if last_trade and last_trade.id > trade.id:
        logger.debug("STALE TRADEBOOK RESULT FOUND. RE-TRYING.")
        return get_last_price(base=base, quote=quote, return_price_only=return_price_only)
    else:
        last_trade = trade

    if return_price_only:
        return trade.price

    logger.info(
        f"LATEST TRADE: {trade.currency_pair} | id={trade.id} | create_time={create_time_formatted} | "
        f"side={trade.side} | amount={trade.amount} | price={trade.price}"
    )
    return trade


def get_min_amount(base, quote):
    """
    Args:
    'DOT', 'USDT'
    """
    try:
        min_amount = spot_api.get_currency_pair(currency_pair=f"{base}_{quote}").min_quote_amount
    except Exception as e:
        logger.error(e)
    else:
        return min_amount


def get_core_cross_margin_data(base='', quote='USDT', data_list=[]):
    for data_dict in data_list:
        if data_dict:
            if data_dict['pair'] == f"{base}_{quote}":
                return data_dict['leverage'], data_dict['max_quote_amount']
    return False, False


def get_cross_margin_data(base='', quote='USDT'):
    leverage, max_quote_amount = False, False
    try:
        data_file_name = "cross_margin_currency_leverage_with_pairing_{0}.json".format(quote)
        with open(data_file_name) as json_file:
            leverage, max_quote_amount = get_core_cross_margin_data(base='', quote='USDT',
                                                                    data_list=json.load(json_file))
            # for data_dict in json.load(json_file):
            #     if data_dict:
            #         if data_dict['pair'] == f"{base}_{quote}":
            #             return data_dict['leverage'], data_dict['max_quote_amount']

            if not leverage and not max_quote_amount:
                leverage, max_quote_amount = get_core_cross_margin_data(base='', quote='USDT',
                                                                        data_list=get_all_cross_margin_pairs_leverage(
                                                                            quote=quote))
            return leverage, max_quote_amount
            # else:
            #     for data_dict in get_all_cross_margin_pairs_leverage(quote=quote):
            #         if data_dict:
            #             if data_dict['pair'] == f"{base}_{quote}":
            #                 return data_dict['leverage'], data_dict['max_quote_amount']
    except Exception as e:
        logger.error(e)
        return False, False


def get_cross_margin_amount(base='', quote='USDT', amount=None, last_price=None):
    leverage, max_quote_amount = get_cross_margin_data(base=base, quote=quote)
    # core_amount = float(amount) / float(last_price)
    borrow_amount = (leverage - 1) * float(amount)
    borrow_amount = max_quote_amount if borrow_amount > float(max_quote_amount) else borrow_amount
    return (float(amount)+borrow_amount)/float(last_price)


def place_order(base, quote, amount, side, last_price, account_type=''):
    """
    Args:
    'DOT', 'USDT', 50, 'buy', 400
    """
    try:
        if account_type == 'spot':
            order = Order(
                amount=str(float(amount) / float(last_price)),
                price=last_price,
                side=side,
                currency_pair=f"{base}_{quote}",
                time_in_force="ioc"
            )
            order = spot_api.create_order(order)
        elif account_type == 'cross_margin':  # if we have an opportunity to  create  a cross margin  order to current coin pair
            if side == 'buy':
                order = Order(
                    # amount=str(float(amount) / float(last_price)),
                    amount=get_cross_margin_amount(base=base, quote=quote,
                                                   amount=float(amount), last_price=float(last_price)),
                    price=last_price,
                    side='buy',
                    currency_pair=f"{base}_{quote}",
                    time_in_force="ioc",
                    type='limit',
                    account='cross_margin',
                    auto_borrow=True,
                    auto_repay=False
                )
            if side == 'sell':
                order = Order(
                    # amount=str(float(amount) / float(last_price)),
                    amount=get_cross_margin_amount(base=base, quote=quote,
                                                   amount=float(amount), last_price=float(last_price)),
                    price=last_price,
                    side='sell',
                    currency_pair=f"{base}_{quote}",
                    time_in_force="ioc",
                    type='limit',
                    account='cross_margin',
                    auto_borrow=False,
                    auto_repay=True
                )

            order = margin_api.create_cross_margin_loan(order)

        t = order
        logger.info(
            f"PLACE ORDER: {t.side} | {t.id} | {t.account} | {t.type} | {t.currency_pair} | {t.status} | "
            f"amount={t.amount} | price={t.price} | left={t.left} | filled_total={t.filled_total} | "
            f"fill_price={t.fill_price} | fee={t.fee} {t.fee_currency}"
        )

    except Exception as e:
        logger.error(e)
        raise

    else:
        return order
