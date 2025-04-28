import time, json
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from pocketoptionapi.stable_api import PocketOption
import pocketoptionapi.global_value as global_value
import pandas as pd
from sklearn.ensemble import RandomForestClassifier



load_dotenv()
# Session configuration
start_counter = time.perf_counter()

# Demo SSID Setup
ssid = os.getenv("""SSID""")
demo = True

min_payout = 90
period = 60  
expiration = 60
INITIAL_AMOUNT = 1
MARTINGALE_LEVEL = 4

WATCHLIST = [
    "EURAUD_otc", "EURCHF_otc", "EURGBP_otc", "EURJPY_otc", "EURUSD_otc",
    "GBPAUD_otc", "GBPJPY_otc", "GBPUSD_otc",
    "AUDCHF_otc", "AUDJPY_otc", "AUDUSD_otc",
    "CADCHF_otc", "AUDCAD_otc", "CHFJPY_otc",
    "USDCAD_otc", "USDCHF_otc", "USDCNH_otc", "USDJPY_otc"
]

api = PocketOption(ssid, demo)
api.connect()

FEATURE_COLS = ['RSI', 'k_percent', 'r_percent', 'MACD', 'MACD_EMA', 'Price_Rate_Of_Change']
PROB_THRESHOLD = 0.76

def get_payout():
    try:
        d = json.loads(global_value.PayoutData)
        for pair in d:
            name = pair[1]
            payout = pair[5]
            if (
                name in WATCHLIST and
                pair[14] == True and
                name.endswith("_otc") and
                len(name) == 10
            ):
                if payout >= min_payout:
                    global_value.pairs[name] = {'payout': payout, 'type': pair[3]}
                elif name in global_value.pairs:
                    del global_value.pairs[name]
        return True
    except:
        return False

def get_df():
    try:
        for i, pair in enumerate(global_value.pairs, 1):
            df = api.get_candles(pair, period)
            global_value.logger(f'{pair} ({i}/{len(global_value.pairs)})', "INFO")
            time.sleep(1)
        return True
    except:
        return False

def make_df(df0, history):
    df1 = pd.DataFrame(history).sort_values(by='time').reset_index(drop=True)
    df1['time'] = pd.to_datetime(df1['time'], unit='s', utc=True)
    df1.set_index('time', inplace=True)

    df = df1['price'].resample(f'{period}s').ohlc().reset_index()

    if df0 is not None:
        ts = datetime.timestamp(df.loc[0]['time'])
        for x in range(len(df0)):
            ts2 = datetime.timestamp(df0.loc[x]['time'])
            if ts2 < ts:
                df = df._append(df0.loc[x], ignore_index=True)
            else:
                break
        df = df.sort_values(by='time').reset_index(drop=True)
    return df

def prepare_data(df):
    df = df[['time', 'open', 'high', 'low', 'close']]
    df.rename(columns={'time': 'timestamp'}, inplace=True)
    df.sort_values(by='timestamp', inplace=True)
    df['change_in_price'] = df['close'].diff()

    rsi_period = 14
    stochastic_period = 14
    macd_ema_long = 26
    macd_ema_short = 12
    macd_signal = 9
    roc_period = 9

    up_df = df['change_in_price'].where(df['change_in_price'] > 0, 0)
    down_df = abs(df['change_in_price'].where(df['change_in_price'] < 0, 0))
    ewma_up = up_df.ewm(span=rsi_period).mean()
    ewma_down = down_df.ewm(span=rsi_period).mean()
    rs = ewma_up / ewma_down
    df['RSI'] = 100.0 - (100.0 / (1.0 + rs))

    df['low_14'] = df['low'].rolling(window=stochastic_period).min()
    df['high_14'] = df['high'].rolling(window=stochastic_period).max()
    df['k_percent'] = 100 * ((df['close'] - df['low_14']) / (df['high_14'] - df['low_14']))
    df['r_percent'] = ((df['high_14'] - df['close']) / (df['high_14'] - df['low_14'])) * -100

    ema_26 = df['close'].ewm(span=macd_ema_long).mean()
    ema_12 = df['close'].ewm(span=macd_ema_short).mean()
    df['MACD'] = ema_12 - ema_26
    df['MACD_EMA'] = df['MACD'].ewm(span=macd_signal).mean()

    df['Price_Rate_Of_Change'] = df['close'].pct_change(periods=roc_period)
    df['Prediction'] = (df['close'].shift(-1) > df['close']).astype(int)

    df.dropna(inplace=True)
    return df

def train_and_predict(df):
    X_train = df[FEATURE_COLS].iloc[:-1]
    y_train = df['Prediction'].iloc[:-1]

    model = RandomForestClassifier(n_estimators=100, oob_score=True, criterion="gini", random_state=0)
    model.fit(X_train, y_train)

    X_test = df[FEATURE_COLS].iloc[[-1]]
    proba = model.predict_proba(X_test)
    call_conf = proba[0][1]
    put_conf = 1 - call_conf

    if call_conf > PROB_THRESHOLD:
        decision = "call"
        emoji = "🟢"
        confidence = call_conf
    elif put_conf > PROB_THRESHOLD:
        decision = "put"
        emoji = "🔴"
        confidence = put_conf
    else:
        global_value.logger("⏭️ Confidence too low — skipping trade.", "INFO")
        return None

    global_value.logger(f"{emoji} === PREDICTED: {decision.upper()} | CONFIDENCE: {confidence:.2%}", "INFO")
    return decision

def perform_trade(amount, pair, action, expiration):
    global_value.logger(f"🚀 TRADE: {amount}, {pair}, {action}, {expiration}s", "INFO")
    result = api.buy(amount=amount, active=pair, action=action, expirations=expiration)
    trade_id = result[1]
    time.sleep(expiration)
    return api.check_win(trade_id)

def martingale_strategy(pair, action):
    amount = INITIAL_AMOUNT
    level = 1
    result = perform_trade(amount, pair, action, expiration)

    if result is None:
        return

    global_value.logger(f"🎲 RESULT: {result[1].upper()} | Trade ID: {result[0]}", "INFO")
    while result[1] == 'loose' and level < MARTINGALE_LEVEL:
        level += 1
        amount *= 2
        global_value.logger(f"❌ LOSS - Martingale Level {level} | Next Amount: {amount}", "INFO")
        result = perform_trade(amount, pair, action, expiration)
        global_value.logger(f"🎲 RESULT: {result[1].upper()} | Trade ID: {result[0]}", "INFO")

    if result[1] != 'loose':
        global_value.logger("✅ WIN - Resetting to base amount.", "INFO")
    else:
        global_value.logger(f"⚠️ Max Martingale level {MARTINGALE_LEVEL} reached. Resetting.", "INFO")

def wait_until_next_candle(period_seconds=300, seconds_before=15):
    while True:
        now = datetime.now(timezone.utc)
        next_candle = ((now.timestamp() // period_seconds) + 1) * period_seconds
        if now.timestamp() >= next_candle - seconds_before:
            break
        time.sleep(0.2)

def wait_for_candle_start():
    while True:
        now = datetime.now(timezone.utc)
        if now.second == 0 and now.minute % (period // 60) == 0:
            break
        time.sleep(0.1)

# ✅ PATCHED STRATEGIE FUNCTION
def strategie():
    pairs_snapshot = list(global_value.pairs.keys())
    for i, pair in enumerate(pairs_snapshot, 1):
        if pair not in global_value.pairs:
            global_value.logger(f"⚠️ Skipping {pair} — pair no longer in global_value.pairs", "WARNING")
            continue

        payout = global_value.pairs[pair].get('payout', 0)
        if payout < min_payout:
            global_value.logger(f"⛔ Skipping {pair} — payout below threshold: {payout}%", "INFO")
            continue

        wait_until_next_candle(period, 15)

        df = make_df(global_value.pairs[pair].get('dataframe'), global_value.pairs[pair].get('history'))
        if df is None or df.empty:
            global_value.logger(f"⚠️ Skipping {pair} — dataframe construction failed or empty.", "WARNING")
            continue

        global_value.logger(f"{len(df)} Candles collected for === {pair} === ({period // 60} mins timeframe)", "INFO")

        processed_df = prepare_data(df.copy())
        if processed_df.empty:
            global_value.logger(f"⚠️ Skipping {pair} — processed dataframe is empty.", "WARNING")
            continue

        decision = train_and_predict(processed_df)

        if decision:
            wait_for_candle_start()
            martingale_strategy(pair, decision)

            get_payout()
            get_df()

def prepare():
    try:
        return get_payout() and get_df()
    except:
        return False

def start():
    while not global_value.websocket_is_connected:
        time.sleep(0.1)
    time.sleep(2)

    if prepare():
        while True:
            strategie()

if __name__ == "__main__":
    start()
    end_counter = time.perf_counter()
    global_value.logger(f"CPU-bound Task Time: {int(end_counter - start_counter)} seconds", "INFO")
