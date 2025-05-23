from airflow import DAG
from airflow.operators.python import PythonOperator, ShortCircuitOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.utils.dates import days_ago
from airflow.models import Variable
from datetime import timedelta, datetime
import requests
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Default arguments for the DAG
default_args = {
    'owner': 'Ik',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

def fetch_crypto_prices():
    url = "https://api.coingecko.com/api/v3/simple/price"
    parameters = {
        'ids': 'bitcoin,ethereum,binancecoin',
        'vs_currencies': 'usd',
        'include_24hr_change': 'true'
    }
    try:
        response = requests.get(url, params=parameters)
        response.raise_for_status()
        data = response.json()

        return [
            {'symbol': 'BTC', 'price_usd': data['bitcoin']['usd'], 'change_24h': data['bitcoin']['usd_24h_change']},
            {'symbol': 'ETH', 'price_usd': data['ethereum']['usd'], 'change_24h': data['ethereum']['usd_24h_change']},
            {'symbol': 'BNB', 'price_usd': data['binancecoin']['usd'], 'change_24h': data['binancecoin']['usd_24h_change']}
        ]
    except requests.RequestException as e:
        raise ValueError(f"Error fetching data from CoinGecko API: {e}")

def insert_into_postgres(**kwargs):
    ti = kwargs['ti']
    crypto_data = ti.xcom_pull(task_ids='fetch_crypto_prices')
    if not crypto_data:
        raise ValueError("No data received from fetch task")

    pg_hook = PostgresHook(postgres_conn_id='postgres_crypto')
    connection = pg_hook.get_conn()
    cursor = connection.cursor()
    
    insert_query = """
    INSERT INTO crypto_prices (symbol, price_usd, change_24h, timestamp)
    VALUES (%s, %s, %s, NOW())
    """
    
    for crypto in crypto_data:
        cursor.execute(insert_query, (crypto['symbol'], crypto['price_usd'], crypto['change_24h']))
    
    connection.commit()
    cursor.close()
    connection.close()

def send_email(**kwargs):
    ti = kwargs['ti']
    crypto_data = ti.xcom_pull(task_ids='fetch_crypto_prices')
    if not crypto_data:
        raise ValueError("No data received from fetch task")
    
    # Generate report as plain text
    report_lines = ["Symbol | Price (USD) | 24h Change (%)", "-" * 40]
    for crypto in crypto_data:
        line = f"{crypto['symbol']} | ${crypto['price_usd']:.2f} | {crypto['change_24h']:.2f}%"
        report_lines.append(line)
    
    report_text = "\n".join(report_lines)
    
    # Retrieve SMTP settings from Airflow Variables
    smtp_host = Variable.get("smtp_host")
    smtp_port = int(Variable.get("smtp_port", default_var=587))
    smtp_username = Variable.get("smtp_username")
    smtp_password = Variable.get("smtp_password")
    sender_email = Variable.get("sender_email")
    receiver_emails = Variable.get("receiver_emails")
    
    # Create email
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_emails
    msg['Subject'] = "Daily Crypto Prices Report"
    
    msg.attach(MIMEText(report_text, 'plain'))
    
    # Send email
    try:
        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.sendmail(sender_email, receiver_emails.split(','), msg.as_string())
        server.quit()
    except Exception as e:
        raise Exception(f"Failed to send email: {str(e)}")

def check_send_time(**kwargs):
    # Send email only at 8 AM daily
    logical_date = kwargs['logical_date']
    return logical_date.hour == 8  # Adjust the hour as needed

with DAG(
    'crypto_price_dag',
    default_args=default_args,
    description='A DAG to fetch cryptocurrency prices and insert them into a PostgreSQL database',
    schedule_interval=timedelta(hours=1),
    start_date=days_ago(1),
    catchup=False,
) as dag:
    
    fetch_task = PythonOperator(
        task_id='fetch_crypto_prices',
        python_callable=fetch_crypto_prices,
        do_xcom_push=True
    )
    
    insert_task = PythonOperator(
        task_id='insert_into_postgres',
        python_callable=insert_into_postgres,
        provide_context=True
    )
    
    check_time_task = ShortCircuitOperator(
        task_id='check_send_time',
        python_callable=check_send_time,
        provide_context=True
    )
    
    send_email_task = PythonOperator(
        task_id='send_email',
        python_callable=send_email,
        provide_context=True
    )
    
    fetch_task >> [insert_task, check_time_task]
    insert_task >> check_time_task
    check_time_task >> send_email_task
