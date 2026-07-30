from symbols import VN100
from market_data import update_symbol
from database import init_database

import time


init_database()


for symbol in VN100:

    print(
        f"Updating {symbol}"
    )

    success = update_symbol(symbol)


    if success:
        print(
            f"✅ {symbol}"
        )
    else:
        print(
            f"❌ {symbol}"
        )


    time.sleep(1)


print("DONE")