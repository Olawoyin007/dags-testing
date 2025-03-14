from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.models import Variable
from airflow.utils.email import send_email
import requests
import json
import pandas as pd
from sqlalchemy import create_engine

# Default arguments for the DAG
default_args = {
    'owner': 'Ik',  # Owner of the DAG
    'depends_on_past': False,  # Do not depend on past runs
    'start_date': datetime(2025, 2, 26),  # Start date for the DAG

}
# Instantiate the DAG
dag = DAG(
    'healthcare_joblist',  # DAG ID
    default_args=default_args,
    description='ETL pipeline for Healthcare job postings from LinkedIn',
    schedule_interval='@daily',  # Runs once per day
    catchup=False,  # Do not backfill past runs
    tags=['healthcare', 'linkedin']  # Tags for categorization
)

def fetch_data(**kwargs):
    """
    Task to fetch data from LinkedIn API.
    Uses Airflow Variables for sensitive credentials.
    Pushes raw data to XCom for downstream tasks.
    """
    try:
        # API configuration
        url = "https://linkedin-jobs-api2.p.rapidapi.com/active-jb-24h"
        querystring = {
            "title_filter": "\"Healthcare Assistant\"",  # Filter for specific job titles
            "location_filter": "\"United Kingdom\"",
            "count": "50"  # Limit the number of results to 50
        }
        headers = {
            "x-rapidapi-key": Variable.get("rapidapi_key"),  # Fetch API key from Airflow Variables
            "x-rapidapi-host": "linkedin-jobs-api2.p.rapidapi.com"
        }
        # Execute API request
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()  # Raise exception for HTTP errors
        # Parse JSON response
        data = response.json()
        # Push raw data to XCom for downstream tasks
        kwargs['ti'].xcom_push(key='raw_data', value=data)
        print(f"Fetched {len(data)} job listings.")
    except Exception as e:
        print(f"Error fetching data: {str(e)}")
        raise

def transform_data(**kwargs):
    """
    Task to transform API response into structured format.
    Pulls raw data from XCom and pushes transformed data to XCom.
    """
    try:
        # Pull raw data from XCom
        ti = kwargs['ti']
        raw_data = ti.xcom_pull(task_ids='fetch_data', key='raw_data')
        
        # Validate raw data
        if not isinstance(raw_data, list) or len(raw_data) == 0:
            raise ValueError("Fetched data is empty or not in expected format (list).")
        
        # Flatten the nested structure and create a DataFrame
        flattened_data = []
        for item in raw_data:
            try:
                record = {
                    "id": item.get("id"),
                    "date_posted": item.get("date_posted"),
                    "title": item.get("title"),
                    "organization": item.get("organization"),
                    "organization_url": item.get("organization_url"),
                    "date_validthrough": item.get("date_validthrough"),
                    "location_country": item["locations_raw"][0]["address"]["addressCountry"] if item.get("locations_raw") else None,
                    "location_locality": item["locations_raw"][0]["address"]["addressLocality"] if item.get("locations_raw") else None,
                    "latitude": item["locations_raw"][0].get("latitude") if item.get("locations_raw") else None,
                    "longitude": item["locations_raw"][0].get("longitude") if item.get("locations_raw") else None,
                    "employment_type": ", ".join(item.get("employment_type", [])),
                    "url": item.get("url"),
                    "linkedin_org_employees": item.get("linkedin_org_employees"),
                    "linkedin_org_size": item.get("linkedin_org_size"),
                    "linkedin_org_industry": item.get("linkedin_org_industry"),
                    "linkedin_org_locations": ", ".join(item.get("linkedin_org_locations", [])),
                    "seniority": item.get("seniority")
                }
                flattened_data.append(record)
            except Exception as e:
                print(f"Error processing record {item.get('id')}: {str(e)}")
                continue
        
        # Convert to DataFrame
        df = pd.DataFrame(flattened_data)
        
        # Validate DataFrame
        if df.empty:
            raise ValueError("No valid records found after transformation.")
        
        # Push transformed data to XCom as JSON
        kwargs['ti'].xcom_push(key='transformed_data', value=df.to_json(orient='records'))
        print(f"Transformed {len(df)} records.")
    except Exception as e:
        print(f"Error transforming data: {str(e)}")
        raise
    
def load_to_postgres(**kwargs):
    """
    Load transformed data into PostgreSQL using PostgresHook's bulk insert.
    """
    try:
        # Pull transformed data from XCom
        ti = kwargs['ti']
        transformed_data = ti.xcom_pull(task_ids='transform_data', key='transformed_data')
        df = pd.read_json(transformed_data, orient='records')

        
        # Initialize PostgresHook
        pg_hook = PostgresHook(postgres_conn_id="postgres_dwh")
        conn = pg_hook.get_conn()
        cursor = conn.cursor()

        # Convert DataFrame to list of tuples (matches table schema)
        data_tuples = [tuple(x) for x in df.to_numpy()]

        # Use PostgreSQL COPY command for efficient bulk insert
        from psycopg2.extras import execute_batch
        
        insert_sql = f"""
            INSERT INTO healthcare_joblist (
                id, date_posted, title, organization, organization_url,
                date_validthrough, location_country, location_locality,
                latitude, longitude, employment_type, url,
                linkedin_org_employees, linkedin_org_size, linkedin_org_industry,
                linkedin_org_locations, seniority
            ) VALUES ({','.join(['%s']*len(df.columns))})
        """

        # Batch insert with execute_batch
        execute_batch(cursor, insert_sql, data_tuples, page_size=100)
        conn.commit()

        print(f"Successfully inserted {len(df)} records")
        
    except Exception as e:
        conn.rollback()
        print(f"Error loading data: {str(e)}")
        raise
    finally:
        cursor.close()
        conn.close()
        
with dag:
    # Task 1: Fetch data from LinkedIn API
    fetch_task = PythonOperator(
        task_id='fetch_data',
        python_callable=fetch_data,
        provide_context=True
    )
    # Task 2: Transform the fetched data
    transform_task = PythonOperator(
        task_id='transform_data',
        python_callable=transform_data,
        provide_context=True
    )
    # Task 3: Load transformed data into PostgreSQL
    load_task = PythonOperator(
        task_id='load_to_postgres',
        python_callable=load_to_postgres,
        provide_context=True
    )

    # Define task dependencies
    fetch_task >> transform_task >> load_task

