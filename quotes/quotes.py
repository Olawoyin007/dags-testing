import requests
import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import DictCursor

# Step 1: Fetch Data from the API
def fetch_quote():
    url = "https://quotes15.p.rapidapi.com/quotes/random/"
    querystring = {"language_code": "en"}
    headers = {
        "x-rapidapi-key": "7b66ced988msh253ab4a526f3148p1eed78jsn4d8bcaa48242",
        "x-rapidapi-host": "quotes15.p.rapidapi.com"
    }

    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()  # Raise an error for bad responses (4xx, 5xx)
        data = response.json()
        print("API Response:", data)  # Debugging: Print the raw response

        # Extract relevant fields
        quote_data = {
            "id": data.get("id") or data.get("quoteId"),  # Ensure correct ID extraction
            "content": data.get("content"),
            "author": data.get("originator", {}).get("name", "Unknown"),
            "tags": ", ".join(data.get("tags", []))  # Convert list to string
        }

        return quote_data

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return None


# Step 2: Transform Data into a DataFrame
def transform_data(quote_data):
    if not quote_data:
        return None  # No data fetched
    return pd.DataFrame([quote_data])


# Step 3: Load Data into PostgreSQL Database using psycopg2
def load_data(df):
    if df is None or df.empty:
        print("No data to insert.")
        return

    # Database connection details
    db_username = "ikeengr"
    db_password = "DataEngineer247"
    db_host = "89.40.0.150"  # Change to your actual database host
    db_port = "5432"
    db_name = "dwh"

    try:
        # Establish connection to the PostgreSQL database
        connection = psycopg2.connect(
            dbname=db_name,
            user=db_username,
            password=db_password,
            host=db_host,
            port=db_port
        )
        connection.autocommit = False  # Disable autocommit for safe transaction handling
        cursor = connection.cursor(cursor_factory=DictCursor)

        # Step 4: Ensure the table and column exist
        # Ensure the 'quotes' table exists with the required 'tags' column
        create_table_query = """
        CREATE TABLE IF NOT EXISTS quotes (
            id BIGINT PRIMARY KEY,
            content TEXT NOT NULL,
            author TEXT,
            tags TEXT
        );
        """
        cursor.execute(create_table_query)
        print("Table 'quotes' created/checked successfully.")

        # Alter table to add 'tags' column if it doesn't exist (for safety)
        alter_table_query = """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'quotes' AND column_name = 'tags') THEN
                ALTER TABLE quotes ADD COLUMN tags TEXT;
            END IF;
        END $$;
        """
        cursor.execute(alter_table_query)
        print("Checked and ensured 'tags' column exists.")

        # Step 5: Insert data using ON CONFLICT (Prevents duplicate entries)
        insert_query = """
        INSERT INTO quotes (id, content, author, tags)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING;
        """

        for _, row in df.iterrows():
            if row["id"] is not None:  # Ensure ID is valid
                cursor.execute(insert_query, (row["id"], row["content"], row["author"], row["tags"]))
                print(f"Inserted row with id={row['id']}")
            else:
                print("Skipping insert: ID is None")

        # Commit the transaction
        connection.commit()
        print("Data successfully inserted.")

    except Exception as e:
        print(f"Error loading data: {e}")
        connection.rollback()  # Rollback if error occurs
    finally:
        cursor.close()
        connection.close()  # Close the connection


# Run the pipeline
if __name__ == "__main__":
    quote = fetch_quote()
    df = transform_data(quote)
    load_data(df)
