import schedule
import time

def run_daily(job, scan_time):

    schedule.every().day.at(scan_time).do(job)

    while True:

        schedule.run_pending()

        time.sleep(1)