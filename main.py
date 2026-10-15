import yfinance as yf

def main():
    symbol = yf.Ticker("GME")
    details = symbol.get_fast_info()
    for i in details.items():
        print(i)

if __name__ == "__main__":
    main()
