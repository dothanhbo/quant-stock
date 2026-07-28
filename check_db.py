from database import load_price_data

df = load_price_data("HPG")

print(df.tail())
print("Total rows:", len(df))