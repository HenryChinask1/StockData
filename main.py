from pathlib import Path
import pandas as pd
import yfinance as yf

MIN_PRICE = 10.0
MAX_PRICE = 300.0
MIN_AVG_VOLUME = 1_000_000
MIN_ATR_PCT = 0.02
TOP_N = 30
CHECK_EARNINGS = False
EARNINGS_DAYS = 2
AVG_VOL_DAYS = 20
ATR_DAYS = 14
HISTORY = "6mo"
UNIVERSE_URL = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv")
OUTPUT_DIR = Path(__file__).parent / "watchlists"

def load_universe():
    df = pd.read_csv(UNIVERSE_URL)
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    return df.set_index("Symbol")[["Security", "GICS Sector"]]

def download_bars(tickers):
    data = yf.download(
        tickers,
        period=HISTORY,
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        threads=True,
        progress=False,
    )
    if data is None or data.empty:
        raise RuntimeError("yfinance returned no data")
    now = pd.Timestamp.now()
    last = data.index[-1]
    if last.date() == now.date() and now.hour < 16:
        data = data.iloc[:-1]
    return data


def metrics(bars):
    bars = bars.dropna(subset=["Close"])
    if len(bars) < max(AVG_VOL_DAYS, ATR_DAYS) + 2:
        return None

    high, low, close, vol = bars["High"], bars["Low"], bars["Close"], bars["Volume"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    last_close = close.iloc[-1]
    avg_vol = vol.iloc[-AVG_VOL_DAYS - 1:-1].mean()
    atr = true_range.iloc[-ATR_DAYS:].mean()

    return {
        "session": bars.index[-1].date(),
        "close": last_close,
        "chg_pct": last_close / prev_close.iloc[-1] - 1,
        "volume": vol.iloc[-1],
        "avg_volume": avg_vol,
        "rel_volume": vol.iloc[-1] / avg_vol if avg_vol else float("nan"),
        "atr": atr,
        "atr_pct": atr / last_close,
        "prev_high": high.iloc[-1],
        "prev_low": low.iloc[-1],
        "high_20d": high.iloc[-20:].max(),
        "low_20d": low.iloc[-20:].min(),
        "pct_from_52w_high": last_close / high.max() - 1,
    }


def last_price(ticker):
    try:
        return yf.Ticker(ticker).fast_info["lastPrice"]
    except Exception:
        return float("nan")


def next_earnings(ticker):
    try:
        dates = yf.Ticker(ticker).calendar.get("Earnings Date") or []
        return min(dates) if dates else None
    except Exception:
        return None


def main():
    universe = load_universe()
    tickers = universe.index.tolist()
    print(f"Universe: {len(tickers)} tickers. Downloading daily bars...")
    data = download_bars(tickers)

    rows, missing = [], []
    for t in tickers:
        try:
            m = metrics(data[t])
        except KeyError:
            m = None
        if m is None:
            missing.append(t)
            continue
        rows.append({"ticker": t, **m})

    df = pd.DataFrame(rows).set_index("ticker").join(universe)
    print(f"Metrics computed for {len(df)}; no/insufficient data for {len(missing)}"
          + (f": {', '.join(missing[:15])}{' ...' if len(missing) > 15 else ''}" if missing else ""))

    passed = df[
        df["close"].between(MIN_PRICE, MAX_PRICE)
        & (df["avg_volume"] >= MIN_AVG_VOLUME)
        & (df["atr_pct"] >= MIN_ATR_PCT)
    ].copy()
    print(f"Passed filters: {len(passed)}")
    if passed.empty:
        print("Nothing passed. Loosen the filters at the top of main.py.")
        return

    passed["score"] = (
        passed["rel_volume"].rank(pct=True)
        + passed["chg_pct"].abs().rank(pct=True)
        + passed["atr_pct"].rank(pct=True)
    ) / 3
    watch = passed.sort_values("score", ascending=False).head(TOP_N)

    print(f"Fetching last price for top {len(watch)}...")
    watch["last_price"] = [last_price(t) for t in watch.index]
    watch["chg_from_close"] = watch["last_price"] / watch["close"] - 1

    if CHECK_EARNINGS:
        print(f"Checking earnings dates for top {len(watch)}...")
        session = pd.Timestamp(watch["session"].iloc[0])
        watch["next_earnings"] = [next_earnings(t) for t in watch.index]
        watch["earnings_soon"] = [
            d is not None and 0 <= (pd.Timestamp(d) - session).days <= EARNINGS_DAYS + 1
            for d in watch["next_earnings"]
        ]

    cols = [
        "Security", "GICS Sector", "session", "score", "close", "chg_pct",
        "last_price", "chg_from_close", "rel_volume", "volume", "avg_volume", "atr", "atr_pct",
        "prev_high", "prev_low", "high_20d", "low_20d", "pct_from_52w_high",
    ] + (["next_earnings", "earnings_soon"] if CHECK_EARNINGS else [])
    watch = watch[cols].round({
        "score": 3, "close": 2, "chg_pct": 4, "last_price": 2,
        "chg_from_close": 4, "rel_volume": 2, "avg_volume": 0,
        "atr": 2, "atr_pct": 4, "prev_high": 2, "prev_low": 2,
        "high_20d": 2, "low_20d": 2, "pct_from_52w_high": 4,
    })

    OUTPUT_DIR.mkdir(exist_ok=True)
    out = OUTPUT_DIR / f"watchlist_{watch['session'].iloc[0]}.csv"
    watch.to_csv(out)

    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(watch[["close", "last_price", "chg_from_close", "chg_pct", "rel_volume", "atr_pct", "prev_high",
                     "prev_low", "score"]
                    + (["earnings_soon"] if CHECK_EARNINGS else [])])
    print(f"\nSaved {len(watch)} rows to {out}")


if __name__ == "__main__":
    main()
