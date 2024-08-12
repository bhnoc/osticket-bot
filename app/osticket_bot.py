import logging
import mysql.connector
import configparser
import datetime
import time
import zoneinfo
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

config = configparser.ConfigParser()
config.read('botconfig.ini')

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler('../logs/osticket_bot.log')
fh.setLevel(logging.INFO)

# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

# create formatter and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)

# add the handlers to the logger
logger.addHandler(fh)
logger.addHandler(ch)


def split_time(t):
    h, m = t.split(':')
    h = int(h)
    m = int(m)
    return (h, m)


def during_hours():
    timezone = config["default"]["timezone"]
    start_time = config["default"]["start_time"]
    stop_time = config["default"]["stop_time"]

    now = datetime.datetime.now(tz=zoneinfo.ZoneInfo(timezone))
    start_h, start_m = split_time(start_time)
    if now.hour < start_h:
        return False
    if now.hour == start_h and now.minute < start_m:
        return False

    stop_h, stop_m = split_time(stop_time)
    if now.hour > stop_h:
        return False
    if now.hour == stop_h and now.minute > stop_m:
        return False

    return True


def report(config, channel, message):
    logger.info(f"channel: {channel} message: {message}")
    client = WebClient(config['slack']['oauth_token'])
    try:
        response = client.chat_postMessage(channel=channel, text=message)
        logger.debug(f"slack response: {response}")
    except SlackApiError as e:
        logger.error(f"got slack API error: {e} response: {response}")
        assert e.response["ok"] is False
        assert e.response["error"]  # str like 'invalid_auth', 'channel_not_found'


def pullTickets(config):
    tickets = None
    logger.debug("pullTickets()")

    # FIXME: make sure our table exists?
    # create table slack_ticket ( ticket_id int unsigned not null, slack_last_notified datetime not null, PRIMARY KEY (ticket_id));

    try:
        connection = mysql.connector.connect(host=config['mysql']['host'],
                                         database=config['mysql']['database'],
                                         user=config['mysql']['user'],
                                         password=config['mysql']['password'])

        interval = "-60 MINUTE"
        sql_select_Query = f"""
        SELECT
            ost_ticket.ticket_id,
            subject,
            CASE
                WHEN closed IS NOT NULL
                    THEN 'Closed Ticket'
                WHEN slack_last_notified IS NULL
                    THEN 'New Ticket'
                WHEN updated < DATE_ADD(NOW(), INTERVAL {interval})
                    THEN 'Update Needed for Ticket'
                ELSE ''
            END AS slack_state,
            closed,
            updated,
            lastupdate,
            slack_last_notified,
            NOW() as now_time
            FROM osticket_db.ost_ticket
                JOIN osticket_db.ost_ticket__cdata
                ON osticket_db.ost_ticket__cdata.ticket_id  = osticket_db.ost_ticket.ticket_id
                LEFT OUTER JOIN osticket_db.slack_ticket
                ON osticket_db.ost_ticket.ticket_id = osticket_db.slack_ticket.ticket_id
            WHERE
                (
                    slack_last_notified IS NULL
                      OR closed IS NULL AND (
                        updated IS NULL
                        OR GREATEST(slack_last_notified, updated) < DATE_ADD(NOW(), INTERVAL {interval})
                      )
                      OR closed IS NOT NULL AND slack_last_notified < closed
                )

        """
        cursor = connection.cursor(dictionary=True)
        cursor.execute(sql_select_Query)
        # get all records
        records = cursor.fetchall()
        tickets = records
    except mysql.connector.Error as e:
        logger.error(f"Error reading data from MySQL table: {e}")
    finally:
        if connection.is_connected():
            connection.close()
            cursor.close()
            logger.debug("MySQL connection is closed")

    return tickets


def updateTicket(config,id):
    logger.info(f"updateTicket({id})")

    try:
        connection = mysql.connector.connect(host=config['mysql']['host'],
                                         database=config['mysql']['database'],
                                         user=config['mysql']['user'],
                                         password=config['mysql']['password'])
        cursor = connection.cursor(dictionary=True)
        update_query = """
        INSERT INTO slack_ticket (ticket_id, slack_last_notified)
          VALUES (%s, now())
          ON DUPLICATE KEY UPDATE slack_last_notified = NOW()"""

        input_data = (id,)
        cursor.execute(update_query,input_data)
        connection.commit()
    except mysql.connector.Error as e:
        logger.error(f"Error reading data from MySQL table: {e}")
    finally:
        if connection.is_connected():
            connection.close()
            cursor.close()
            logger.debug("MySQL connection is closed")


def main():
    logger.info("main()")
    running = True

    print(config.sections())

    prev_in_hours = None
    while running:
        in_hours = during_hours()
        if prev_in_hours is not None and prev_in_hours != in_hours:
            message = f"during hours changed from {prev_in_hours} to {in_hours}"
            report(config, config['slack']['channel'], message)
            logger.info(message)
        prev_in_hours = in_hours
        if not in_hours:
            logger.info("outside working hours")
            time.sleep(60)
            continue

        tickets = pullTickets(config)
        for ticket in tickets:
            logger.info(ticket)
            url = config['default']['url'] 
            message = (
                f"{ticket['slack_state']}: {ticket['subject']}, "
                f"url: https://{url}/upload/scp/tickets.php?id={ticket['ticket_id']}"
            )
            report(config,config['slack']['channel'],message)
            updateTicket(config, ticket["ticket_id"])
        time.sleep(10)



if __name__ == "__main__":
    main()
