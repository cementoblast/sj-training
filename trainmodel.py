from os import getenv, remove
from sys import exit
from decimal import Decimal
from time import sleep
from math import floor
from pandas import DataFrame, read_csv, to_datetime, concat, set_option, date_range
from numpy import random
from bs4 import BeautifulSoup as BS
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
import requests
from json import loads as jloads
from dropbox import Dropbox
from dropbox.files import WriteMode
from dropbox.exceptions import ApiError, AuthError
import logging
import base64
import shioaji as sj
# 設定日誌格式與層級
logging.basicConfig(
    level=logging.INFO,  # 改為 logging.WARNING 則只會顯示警告與錯誤
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

set_option('display.max_rows', None)
def SendMail(contents):
    from_address, to_address = getenv('USER'), [getenv('USER'), getenv('USER2')]
    mail = MIMEMultipart()
    mail["From"], mail['To'], mail['Subject'] = from_address, ", ".join(to_address), "API通知"
    mail.attach(MIMEText(contents))
    smtpserver = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    smtpserver.ehlo()
    smtpserver.login(from_address, getenv('APP_PWD'))
    smtpserver.sendmail(from_address, to_address, mail.as_string())
    smtpserver.quit()
def etf_chg_pr(pr: Decimal, chg: Decimal):
    assert type(chg) == Decimal and type(pr) == Decimal
    raw_newpr = Decimal(round(pr * (Decimal('1') + chg), 2))
    if raw_newpr > Decimal('50'):
        new_pr = str(raw_newpr)
        if '.' not in new_pr:
            new_pr = new_pr + '.00'
        else:
            split_lt = new_pr.split('.')
            if len(split_lt[1]) == 1:
                new_pr = new_pr + '0'
        last_int = int(new_pr[-1])
        if last_int <= 4:
            return Decimal(new_pr[:-1])
        elif last_int == 5:
            return Decimal(new_pr)
        else:
            last_int2 = int(new_pr[-2])
            return Decimal(new_pr[:-2] + str(last_int2 + 1))
    else:
        return raw_newpr
def get_monthly_first_dates(first_date_str: str, today_str: str) -> list:
    """
    輸入起始日與結束日字串 (格式 YYYYMMDD)，
    回傳期間內每個月第一天的 YYYYMMDD 字串列表。
    """
    #將字串轉換為 datetime 物件，確保 pandas 能進行運算
    start = to_datetime(first_date_str)
    end = to_datetime(today_str)
    #產生日期範圍，頻率為 'MS' (Month Start)
    date_series = date_range(start=start, end=end, freq='MS')
    #轉換回指定的字串格式並轉為 list
    return date_series.strftime('%Y%m%d').tolist()
def convert_to_monthly_df(input_df: DataFrame) -> DataFrame:
    input_df['date'] = to_datetime(input_df['date'])
    monthly_df = input_df.sort_values('date').groupby(
        input_df['date'].dt.to_period('M')
    ).tail(1).copy()
    monthly_df = monthly_df[['date', 'close']]
    return monthly_df.reset_index(drop=True)
def get_tw_OHLC(OHLC_url: str, tw_date: str, try_count: int):
    rng = random.default_rng()
    try:
        res = requests.get(OHLC_url, headers=hd, timeout=(3, 10))
        res.raise_for_status()
        jdata = res.json()
        tw_ohlc = jdata['data']
        tw_fields = jdata['fields']
        if jdata['stat'] == 'OK' and tw_fields == ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"] and len(tw_ohlc) != 0:
            return tw_ohlc
        else:
            raise Exception('taiex OHLC stat:', jdata['stat'], '\n欄位(field):', tw_fields, '\nOHLC data:', tw_ohlc)
    except Exception as err:
        print(f"Failed to get the data of {tw_date} with the error of {err}")
        if try_count < 10:
            random_float = rng.uniform(60 + try_count * 30, 75 + try_count * 30)
            sleep(random_float)
            try_count += 1
            print(f'Try again, try_count: {try_count}')
            return get_tw_OHLC(OHLC_url, tw_date, try_count)
        else:
            raise ValueError('Cannot access TW data after 10 trials')
def is_tw_market_open(time_now: datetime) -> bool:
    # 1. 取得現在的台灣時間 (UTC+8)，格式為 YYYYMMDD
    today_str = time_now.strftime("%Y%m%d")
    print(f'Today: {today_str}')
    # 2. 證交所即時報價 API (指定抓取 tse_t00.tw 也就是大盤加權指數)
    tw_info_url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw&json=1&delay=0"
    try:
        # 設定 timeout，防止 API 臨時無回應卡死程式
        response = requests.get(tw_info_url, headers = hd, timeout = 10)
        data = response.json()
        # 3. 從回傳的 JSON 中萃取「交易所紀錄的最新交易日 (d)」
        tw_trade_dt = data['msgArray'][0]['d']
        print('tw trade dt:', tw_trade_dt)
        # 4. 比對日期
        if tw_trade_dt == today_str:
            print(f"✅Open: tw_trade_dt == today_str")
            return True
        else:
            print(f"⏸️Closed: tw_trade_dt != today_str")
            return False
    except Exception as err:
        print(f"⚠️TW info url shows messages with errors: {err}")
    try:
        yahoo_url = "https://tw.stock.yahoo.com/quote/^TWII"
        response = requests.get(yahoo_url, headers = hd, timeout = 10)

        if response.status_code == 200:
            soup = BS(response.text, 'html.parser')
            # 尋找頁面中包含 "資料時間" 的文字區塊 (例如：資料時間：2026/04/18 08:30)
            time_element = soup.find(string=re.compile("資料時間"))
            today_str_yahoo = time_now.strftime("%Y/%m/%d")
            if time_element:
                # 判斷今天的日期字串 (例如 "2026/04/18") 是否包含在裡面
                if today_str_yahoo in time_element:
                    print(f"✅Open, according to TW info from Yahoo ({time_element})")
                    return True
                else:
                    print(f"⏸️Closed, according to TW info from Yahoo ({time_element})")
                    return False
            else:
                print("⚠️Failed to get the time tag in Yahoo")
    except Exception as err:
         print(f"⚠️Yahoo URL shows messages with errors: {err}")
def train(date_tdy):
    def upload_data(dbx, local_file_path, dropbox_path):
        with open(local_file_path, "rb") as f:
            try:
                dbx.files_upload(f.read(), dropbox_path, mode=WriteMode("overwrite"))
            except ApiError as err:
                if err.error.is_path() and err.error.get_path().error.is_insufficient_space():
                    exit("ERROR: Cannot back up; insufficient space.")
                elif err.user_message_text:
                    print(err.user_message_text)
                    exit()
                else:
                    print(err)
                    exit()
    def download_data(dbx, local_file_path, dropbox_file_path):
        with open(local_file_path, "wb") as f:
            metadata, result = dbx.files_download(path=dropbox_file_path)
            f.write(result.content)
        return local_file_path
    def trading(trader, trade_action):
        for odd in (True, False):
            api.quote.subscribe(stk, quote_type='bidask', version=sj.constant.QuoteVersion.v1, intraday_odd=odd)
        api.quote.subscribe(stk, quote_type='tick', version=sj.constant.QuoteVersion.v1, intraday_odd=False)
        api.quote.set_on_tick_stk_v1_callback(trader.tick_callback)
        api.quote.set_on_bidask_stk_v1_callback(trader.bidask_callback)
        api.set_order_callback(trader.place_cb)
        time_now = datetime.now()
        print('now:', time_now)
        try:
            while time_now < last_trade_t:
                if trader.order_dict != dict():
                    trade_dict = trader.trade_obj_dict
                    for trade_id in tuple(trade_dict):
                        trade_obj, expire_t = trade_dict[trade_id][0], trade_dict[trade_id][1]
                        if datetime.now() >= expire_t:
                            api.update_status(api.stock_account, trade_obj)
                            latest_status = trade_obj.status.status
                            print("latest status of trade:", latest_status)
                            if latest_status == "Submitted":
                                api.cancel_order(trade_obj, timeout = 10000)
                                print('Cancel the trade')
                                #sleep(0.5)
                                api.update_status(api.stock_account, trade_obj, timeout = 10000)
                                latest_status = trade_obj.status.status
                                print("latest status of the trade after cancelling:", latest_status)
                                if latest_status == "Filled":
                                    del trader.order_dict[trade_id]
                                    del trader.trade_obj_dict[trade_id]
                                    print("The order is a done deal, cancelling is not allowed.")
                                elif latest_status == "Cancelled":
                                    del trader.order_dict[trade_id]
                                    del trader.trade_obj_dict[trade_id]
                                    print(f"The order was cancelled.")
                            elif latest_status == "Filled":
                                filled_msg_time = datetime.now().strftime('%Y%m%d-%H:%M:%S')
                                del trader.order_dict[trade_id]
                                del trader.trade_obj_dict[trade_id]
                                print(f"The deal {trade_id} is done. {filled_msg_time}")
                                print("Trade Object:\n", trade_obj)
                            break
                if (trade_action == 'Buy' and trader.update_avail_cash() < minimal_order_val and trader.ready_to_buy_amt < 1 and trader.ready_to_sell_amt < 1):
                    print('latest avail cash:', trader.update_avail_cash())
                    print('latest ready buy amt:', trader.ready_to_buy_amt)
                    print('latest ready sell amt:', trader.ready_to_sell_amt)
                    print("No available cash -> Log out")
                    break
                time_now = datetime.now()
            if close_t > time_now:
                sleep((close_t - time_now).seconds)
            print("Time is up -> Log out")
        except KeyboardInterrupt:
            print('Keyboard Interrupt')
            #raise Exception
    def get_nq_data(nq_df: DataFrame, nq_last_date: datetime) -> DataFrame: #format = 2023-07-04 start date必須是上次紀錄的最後一天+1 因為回傳的json檔包含start date
        start_date, now_date = (nq_last_date + timedelta(days = 1)).strftime('%Y-%m-%d'), datetime.today().strftime('%Y-%m-%d')
        if start_date != now_date:
            try:
                nq_url = f'https://api.nasdaq.com/api/quote/COMP/historical?assetclass=index&fromdate={start_date}&limit=9999&todate={now_date}&random=8'
                res = requests.get(nq_url, headers = hd, timeout = (5, 15))
                res.raise_for_status()
                json_data = res.json()
                nq_status = json_data.get('status', {})
                nq_rCode = nq_status.get('rCode')
                if nq_rCode != 200:
                    print('NQ status:\n', nq_status)
                    print("Abnormal status!")
                    return nq_df
                else:
                    print('NQ status code=200')
                    rows = json_data.get('data', {}).get('tradesTable', {}).get('rows')
                    if not rows:
                        print("No new data of NQ")
                        return nq_df
                    new_df = DataFrame(rows)
                    # --- 資料清洗 ---
                    # 1. 整理欄位名稱並只保留 date 和 close
                    new_df = new_df[['date', 'close']]
                    # 2. 清洗 close 數值：移除逗號 (如 "16,000.50" -> 16000.50) 並轉為浮點數
                    new_df['close'] = new_df['close'].astype(str).str.replace(',', '').astype(float)
                    # 3. 統一日期格式 (Nasdaq API 回傳格式為 MM/DD/YYYY)
                    new_df['date'] = to_datetime(new_df['date'])
                    nq_df['date'] = to_datetime(nq_df['date'])
                    # --- 合併與排序 ---
                    merged_df = concat([nq_df, new_df], ignore_index = True)
                    merged_df = merged_df.drop_duplicates(subset = ['date'], keep = 'last')
                    merged_df = merged_df.sort_values(by = 'date').reset_index(drop = True)
                    print(f"Successfully get the new data, total rows: {len(merged_df)}")
                    return merged_df
            except requests.exceptions.HTTPError as errh:
                print(f"HTTP error: {errh}")
                return nq_df
            except Exception as err:
                print(f"Failed to get the NQ data: {err}")
                return nq_df
        else:
            print('NQ: No data to update')
            return nq_df
    dbx = Dropbox(app_key=getenv('DBX_K'),
                app_secret=getenv('DBX_SCRT'),
                oauth2_refresh_token=getenv('DBX_REFRESH'))
    try:
        dbx.users_get_current_account()
    except AuthError:
        exit("ERROR: Invalid access token; try re-generating an access token from the app console on the web.")

    codes_dict, index_code_lt, tw_new_data = dict(), ['1000', 'nq'], []
    for index_code in index_code_lt:
        dbx_pname = f"/p{index_code}.csv"
        codes_dict[index_code] = {'dbx': dbx_pname, 'local': f"p{index_code}.csv"}
        index_df = read_csv(download_data(dbx, codes_dict[index_code]['local'], codes_dict[index_code]['dbx']), parse_dates=['date'])
        index_df['date'] = to_datetime(index_df['date'])
        index_df = index_df.sort_values(by = 'date').reset_index(drop = True)
        last_date = index_df['date'].max()
        codes_dict[index_code]['last_date'] = last_date
        codes_dict[index_code]['df'] = index_df

    tw_all_df = codes_dict['1000']['df'].copy()
    tw_new_dates_lt = get_monthly_first_dates(f"{codes_dict['1000']['last_date'].strftime('%Y%m')}01", date_tdy)
    #print('TW new dates list:\n', tw_new_dates_lt)
    for tw_date in tw_new_dates_lt:
        OHLC_url = f"https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?response=json&date={tw_date}"
        tw_new_data.extend(get_tw_OHLC(OHLC_url, tw_date, 0))
        rng = random.default_rng()
        random_float = rng.uniform(30, 40)
        sleep(random_float)
    tw_new_df = DataFrame(tw_new_data, columns=['date', 'open', 'high', 'low', 'close'])
    if tw_new_df.shape[0] > 0:
        # 3.1 處理民國年轉西元年 ('115/03/01' -> '2026/03/01')
        tw_new_df['date'] = tw_new_df['date'].apply(
            lambda x: str(int(x.split('/')[0]) + 1911) + '/' + x.split('/')[1] + '/' + x.split('/')[2]
        )
        tw_new_df['date'] = to_datetime(tw_new_df['date'])
        # 3.2 處理字串轉浮點數 (去除千分位逗號)
        cols_to_float = ['open', 'high', 'low', 'close']
        for col in cols_to_float:
            tw_new_df[col] = tw_new_df[col].str.replace(',', '').astype(float)
        # 步驟 4：合併
        tw_all_df = concat([codes_dict['1000']['df'], tw_new_df], ignore_index=True)
        # 步驟 5：去除重複項並排序 (保護機制：避免日期區間重疊導致資料重複)
        tw_all_df = tw_all_df.drop_duplicates(subset = ['date'], keep = 'last')
        tw_all_df = tw_all_df.sort_values(by = 'date').reset_index(drop = True)
        codes_dict['1000']['df'] = tw_all_df
        codes_dict['1000']['df'].to_csv(codes_dict['1000']['local'], index = False)
        print("Merged TW data")
    else:
        print("No new TW data")
    codes_dict['nq']['df'] = get_nq_data(codes_dict['nq']['df'], codes_dict['nq']['last_date'])
    codes_dict['nq']['df'].to_csv(codes_dict['nq']['local'], index = False)
    for index_code in codes_dict:
        upload_data(dbx, codes_dict[index_code]['local'], codes_dict[index_code]['dbx'])
    tw_mon_df = convert_to_monthly_df(tw_all_df)
    sma55 = tw_mon_df['close'].tail(55).mean()
    bias55 = tw_all_df['close'].iloc[-1] / sma55 - 1
    if (bias55 > 0) & (bias55 <= 1):
        print(f"Bias 55 is about {round(bias55 * 100, 2)}%, start real-time monitoring")
        close_t = datetime(date_tdy.year, date_tdy.month, date_tdy.day, 5)
        last_trade_t = datetime(date_tdy.year, date_tdy.month, date_tdy.day, 5)
        api = sj.Shioaji(simulation=False)
        api.logout()
        api.login(api_key = getenv('SJ_API_KEY'), secret_key = getenv('SJ_SECRET_KEY'),
        contracts_cb=lambda security_type: print(f"Art style: {'.'.join(str(security_type)[-5:-2].upper())}"))
        pfx_path = "temp_cert.pfx"
        with open(pfx_path, "wb") as f:
            f.write(base64.b64decode(getenv('SJ_CERT_BASE64')))
        result = api.activate_ca(ca_path = pfx_path, ca_passwd = getenv('SJ_CERT_PASSWORD'), person_id = getenv('SJ_ID'))
        print("ca:", result)
        tse_contract = api.Contracts.Indexs.TSE["001"]
        tse_update_dt = tse_contract.update_date
        print('tse update date:', tse_update_dt, type(tse_update_dt))
        print('tse contract:', tse_contract)
        tse_dt = datetime.fromtimestamp(int(str(api.snapshots(tse_contract)[0].ts)[:10]))
        print("tse datetime:", tse_dt)

        if is_tw_market_open(date_tdy):
            balance = api.account_balance(timeout=100000)
            if balance.errmsg != '':
                print("Account error message:", balance.errmsg)
                raise Exception("There is an error message in the account.")
            settle_lt = api.settlements(timeout = 100000)
            cash = Decimal(balance.acc_balance + settle_lt[1].amount + settle_lt[2].amount)
            trade_lt, minimal_order_val, pos_qty = api.list_trades(), 702, Decimal(0)
            pos_lt = api.list_positions(api.stock_account, unit=sj.constant.Unit.Share, timeout = 100000)
            for pos in pos_lt:
                if pos.code == stk_code:
                    pos_qty = Decimal(pos.quantity)
            print(f"cash:{cash}", "trade lt:", trade_lt, f"shares of {stk_code}:", pos_qty)
            stk = api.Contracts.Stocks[stk_code]
            snap_data = api.snapshots([stk])[0]
            open_pr, buy_pr, sell_pr, avg_pr = round(Decimal(snap_data.open), 2), round(Decimal(snap_data.buy_price), 2), round(Decimal(snap_data.sell_price), 2), round(Decimal(snap_data.average_price), 2)
            trade_action = 'Buy'
            trader = Trader(open_pr, buy_pr, sell_pr, avg_pr, trade_action, pos_qty, cash, api)
            #trading(trader, trade_action)
            api.logout()
            print('Log out')
            remove(pfx_path)
        else:
            api.logout()
            print('Not open')
    else:
        print(f"Bias 55 is about {round(bias55 * 100, 2)}%, no need for real-time monitoring")
class Trader:
    def __init__(self, open_pr: Decimal, buy_pr: Decimal, sell_pr: Decimal, avg_pr: Decimal, action: str, pos_qty: Decimal, cash: Decimal, api):
        self.__action = action
        self.__open = open_pr
        self.__stk_ratio = Decimal(0.9)
        self.cash = cash
        self.pos_qty = pos_qty
        self.order_dict = dict()
        self.only_buy_odd = True
        self.bid_pr = buy_pr
        self.bid_odd_pr = buy_pr
        self.bid_odd_vol = 1
        self.ask_pr = sell_pr
        self.ask_odd_pr = sell_pr
        self.ask_odd_vol = 1
        self.high = 0
        self.low = 0
        self.close = buy_pr
        self.avg = avg_pr
        self.ready_to_buy_amt = 0
        self.bought_amt = 0
        self.ready_to_sell_amt = 0
        self.sold_amt = 0
        self.ready_to_sell_qty = 0
        self.sold_qty = 0
        self.trade_obj_dict = dict()
        self.__action_dict = {'Buy': {'00675L': self.buy}, 'Sell': {'00675L': self.sell, '006208': self.sell_tse}}
        self.__api = api
        self.__stk = api.Contracts.Stocks[stk_code]
        self.__minimal_order_val = 702
        self.__minimal_qty = (cash + floor(floor(pos_qty * buy_pr) * sell_fee_ratio)) * self.__stk_ratio // buy_pr if stk_code == '00675L' else 441
    def update_avail_cash(self):
        avail = self.cash - self.ready_to_buy_amt - self.bought_amt + self.sold_amt
        if avail < 0:
            raise Exception("There is no available cash!!!")
        return avail
    def update_pos_qty(self):
        latest_qty = self.pos_qty - self.ready_to_sell_qty - self.sold_qty
        if latest_qty < 0:
            raise Exception("There is no enough quantity to sell!!!")
        return latest_qty
    @property
    def get_stk_ratio(self):
        return self.__stk_ratio
    def get_balance(self):
        settle_lt = self.__api.settlements(timeout = 100000)
        return self.__api.account_balance(timeout = 100000).acc_balance + settle_lt[1].amount + settle_lt[2].amount
    def place_cb(self, stat, msg):
        print("Report:", stat, msg)
        msg_time = datetime.now().strftime('%Y%m%d-%H:%M:%S')
        if "operation" in msg:
            op_type, op_code, action = (
                msg["operation"]["op_type"],
                msg["operation"]["op_code"],
                msg["order"]["action"])
            order_pr, order_qty, fold = (
                round(Decimal(msg["order"]["price"]), 2),
                Decimal(msg["order"]["quantity"]),
                fold_dict[msg["order"]["order_lot"]])
            stk_code, total_shares = msg['contract']['code'], order_qty * fold
            stk_val = floor(order_pr * total_shares)
            if op_code == "00":
                if action == "Buy":
                    transaction_amt = floor(stk_val * buy_fee_ratio)
                    if op_type == "New":
                        print(f"New: Create image {stk_code} No.{order_pr} for {total_shares} elements")
                    elif op_type == "Cancel":
                        self.ready_to_buy_amt -= transaction_amt
                        print(f"Modify {stk_code} No.{order_pr} for {total_shares} elements")
                else:
                    transaction_amt = floor(stk_val * sell_fee_ratio)
                    if op_type == "New":
                        print(f"New: Transfer image {stk_code} No.{order_pr} for {total_shares} elements")
                    elif op_type == "Cancel":
                        self.ready_to_sell_amt -= transaction_amt
                        self.ready_to_sell_qty -= total_shares
                        print(f"Modify {stk_code} No.{order_pr} for {total_shares} elements")
                direction = 'Pay' if action == 'Buy' else 'Receive'
                contents = f"{stk_code}\n{op_type} {action} at {order_pr} for {total_shares} shares\nTotal amount: {direction} ${transaction_amt}\n{msg_time}"
                SendMail(contents)
            else:
                print(op_type, op_code)
                raise Exception(msg["operation"]["op_msg"])
        else:
            order_pr, trade_vol, action, order_lot = (round(Decimal(msg["price"]), 2), Decimal(msg["quantity"]), msg["action"], msg["order_lot"])
            stk_code, total_shares = msg['code'], trade_vol * fold_dict[order_lot]
            stk_val = floor(order_pr * total_shares)
            if action == "Buy":
                transaction_amt = floor(stk_val * buy_fee_ratio)
                self.bought_amt += transaction_amt
                self.ready_to_buy_amt -= transaction_amt
            else:
                transaction_amt = floor(stk_val * sell_fee_ratio)
                self.sold_amt += transaction_amt
                self.ready_to_sell_amt -= transaction_amt
                self.sold_qty += total_shares
                self.ready_to_sell_qty -= total_shares
            print(f"Successful testing\n{action} {stk_code} No.{order_pr} for {total_shares} elements")
            direction = 'Pay' if action == 'Buy' else 'Receive'
            contents = f"{stk_code}\nSuccessfully {action} at {order_pr} for {total_shares} shares\nTotal amount: {direction} ${transaction_amt}\n{msg_time}"
            SendMail(contents)
    def PlaceOrder(self, pr: Decimal, qty: Decimal, act: str, lot: str, stk, amt: Decimal):
        if stk == self.__stk:
            avail_cash, avail_qty = self.update_avail_cash(), self.update_pos_qty()
            if (act == 'Buy' and amt <= avail_cash) or (act == 'Sell' and avail_qty - qty * 1000 >= self.__minimal_qty):
                order = self.__api.Order(price = pr, quantity = qty, action = act, price_type = "LMT",
                order_type = "ROD", order_cond = 'Cash', order_lot = lot, account = self.__api.stock_account)
                trade = self.__api.place_order(stk, order)
                if act == 'Buy':
                    self.ready_to_buy_amt += amt
                    self.trade_obj_dict[trade.order.id] = [trade, trade.status.order_datetime + timedelta(seconds=1800)]#[trade_object, expire_time]
                else:
                    self.ready_to_sell_amt += amt
                    self.ready_to_sell_qty += qty * 1000#Qty=lot if Sell
                    self.trade_obj_dict[trade.order.id] = [trade, trade.status.order_datetime + timedelta(seconds=16200)]#[trade_object, expire_time]
                self.order_dict[trade.order.id] = pr
            else:
                if act == 'Buy':
                    contents = f'No enough cash to buy\nPrice: {pr} Vol: {qty} Lot: {lot} Amount: {amt} Available cash: {avail_cash}'
                else:
                    contents = f'No enough quantity to Sell\nPrice: {pr} Vol: {qty} Lot: {lot} Amount: {amt} Available quantity: {avail_qty}'
                SendMail(contents)
        else:
            contents = f'Warning! Unexpected stock to {act.lower()}: {stk.code}\nPrice: {pr} Vol: {qty} Lot: {lot} Amount: {amt}'
            SendMail(contents)
    def sell_tse(self):
        latest_qty, tick_chg = self.update_pos_qty(), 1
        while latest_qty > self.__minimal_qty:
            new_pr = etf_chg_pr(76.6, tick_chg)
            if new_pr not in self.order_dict.values():
                amt = floor(new_pr * 1000 * sell_fee_ratio)
                self.PlaceOrder(new_pr, 1, 'Sell', 'Common', self.__stk, amt)
            tick_chg += 1
    def sell(self):
        latest_qty = self.update_pos_qty()
        stk_pr = self.close * sell_fee_ratio
        current_stk_val = latest_qty * stk_pr
        assets_val = current_stk_val + self.cash - self.ready_to_buy_amt - self.bought_amt + self.sold_amt + self.ready_to_sell_amt
        target_stk_val = assets_val * self.__stk_ratio
        sell_lot = floor((current_stk_val - target_stk_val) / stk_pr / 1000)
        if sell_lot >= 1 and self.ask_pr >= self.ask_odd_pr:
            if latest_qty - sell_lot * 1000 >= self.__minimal_qty:
                new_pr = etf_chg_pr(self.ask_pr, -1)
                if new_pr > self.avg and new_pr not in self.order_dict.values():
                    amt = floor(new_pr * sell_lot * 1000 * sell_fee_ratio)
                    self.PlaceOrder(new_pr, sell_lot, 'Sell', 'Common', self.__stk, amt)
            else:
                print('No enough qty to sell')
    def buy(self):
        avail_cash = self.cash - self.ready_to_buy_amt - self.bought_amt + self.sold_amt
        if avail_cash >= self.__minimal_order_val:
            ref_pr = self.avg - Decimal(0.05)
            #print('bid odd:', self.bid_odd_pr, 'ref pr:', ref_pr, 'bid odd <= ref pr:', self.bid_odd_pr <= ref_pr)
            #print('bid:', self.bid_pr, 'bid odd:', self.bid_odd_pr, 'bid >= bid odd:', self.bid_pr >= self.bid_odd_pr)
            if self.only_buy_odd and self.bid_pr > self.bid_odd_pr and self.bid_odd_pr <= ref_pr:  # 買零股
                new_pr = etf_chg_pr(self.bid_odd_pr, 1)
                buy_all_val = floor(floor(new_pr * self.ask_odd_vol) * buy_fee_ratio)
                if new_pr not in self.order_dict.values():
                    if buy_all_val > avail_cash:
                        order_shares = Decimal(avail_cash // floor(new_pr * buy_fee_ratio))
                        print('符合第三層條件')
                        self.PlaceOrder(new_pr, order_shares, 'Buy',
                        "IntradayOdd", self.__stk, floor(floor(new_pr * order_shares) * buy_fee_ratio))
                    elif self.__minimal_order_val <= buy_all_val <= avail_cash:
                        order_shares = self.ask_odd_vol
                        print('符合第三層條件')
                        self.PlaceOrder(new_pr, order_shares, 'Buy',
                        "IntradayOdd", self.__stk, buy_all_val)
            elif not self.only_buy_odd and self.bid_pr <= self.bid_odd_pr and self.bid_pr <= ref_pr:  # 買整股
                new_pr = etf_chg_pr(self.bid_pr, 1)
                if new_pr not in self.order_dict.values():
                    total_cost = floor(new_pr * buy_fee_ratio * fold_dict["Common"])
                    buy_lot = avail_cash // total_cost
                    if buy_lot >= 1:
                        self.PlaceOrder(new_pr, buy_lot, 'Buy',
                        "Common", self.__stk, total_cost)
    def tick_callback(self, exchange, tick):
        self.close, self.avg, self.high, self.low = tick.close, tick.avg_price, tick.high, tick.low#All Decimal
        total_cost = self.close * buy_fee_ratio * fold_dict["Common"]
        avail_cash = (self.cash - self.ready_to_buy_amt - self.bought_amt + self.sold_amt)
        buy_lot = avail_cash // total_cost
        if buy_lot >= 1:
            self.only_buy_odd = False
        return tick
    def bidask_callback(self, exchange, bidask):
        if bidask.intraday_odd == 1:
            self.bid_odd_pr = bidask.bid_price[0]
            self.ask_odd_pr = bidask.ask_price[0]
            self.bid_odd_vol = bidask.bid_volume[0]
            self.ask_odd_vol = bidask.ask_volume[0]
        else:
            self.bid_pr = bidask.bid_price[0]
            self.ask_pr = bidask.ask_price[0]
        self.__action_dict[self.__action][stk_code]()
        return bidask
buy_fee_ratio, sell_fee_ratio = Decimal(1.001425), Decimal(0.997575)
fold_dict = {"Common": Decimal(1000),
"IntradayOdd": Decimal(1), Decimal(0): Decimal(1000), Decimal(1): Decimal(1)}
stk_code = '00675L'
hd = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.97 Safari/537.36"}
if __name__ == "__main__":
    date_tdy = datetime.now()
    print(date_tdy)
    print('Start training model')
    train(date_tdy)