import sqlite3
import pandas as pd
import logging
import os
import time
from datetime import datetime
from ingestion_db import ingest_db

# 1. Create the 'logs' folder if it doesn't already exist
os.makedirs("logs", exist_ok=True)

# 2. Force Jupyter to remove its default hidden loggers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# 3. Apply your custom logging configuration
logging.basicConfig(
    filename="logs/get_vendor_summary.log", 
    level=logging.DEBUG, 
    format="%(asctime)s - %(levelname)s - %(message)s", 
    filemode="a"
)

def optimize_database(conn):
    '''Creates indexes on frequently joined and grouped columns to speed up query execution.'''
    cursor = conn.cursor()
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchases_vendor_brand ON purchases(VendorNumber, Brand);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sales_vendor_brand ON sales(VendorNo, Brand);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchase_prices_brand ON purchase_prices(Brand);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendor_invoice_vendor ON vendor_invoice(VendorNumber);")
    conn.commit()
    logging.info("Database indexes verified/created successfully.")

def create_vendor_summary(conn):
    '''Merges the different tables to get the overall vendor summary and adds calculated columns.'''
    
    query = """
    WITH FreightSummary AS (
        SELECT VendorNumber, SUM(Freight) AS FreightCost 
        FROM vendor_invoice 
        GROUP BY VendorNumber 
    ), 
    PurchaseAgg AS (
        SELECT VendorNumber, VendorName, Brand, Description, PurchasePrice, 
               SUM(Quantity) AS TotalPurchaseQuantity, SUM(Dollars) AS TotalPurchaseDollars 
        FROM purchases 
        WHERE PurchasePrice > 0 
        GROUP BY VendorNumber, VendorName, Brand, Description, PurchasePrice 
    ), 
    SalesSummary AS (
        SELECT VendorNo, Brand, SUM(SalesQuantity) AS TotalSalesQuantity, 
               SUM(SalesDollars) AS TotalSalesDollars, SUM(SalesPrice) AS TotalSalesPrice, 
               SUM(ExciseTax) AS TotalExciseTax 
        FROM sales 
        GROUP BY VendorNo, Brand
    )
    SELECT 
        pa.VendorNumber, 
        pa.VendorName, 
        pa.Brand, 
        pa.Description, 
        pa.PurchasePrice, 
        pp.Price AS ActualPrice,     
        pp.Volume,                   
        pa.TotalPurchaseQuantity, 
        pa.TotalPurchaseDollars, 
        ss.TotalSalesQuantity, 
        ss.TotalSalesDollars, 
        ss.TotalSalesPrice, 
        ss.TotalExciseTax, 
        fs.FreightCost 
    FROM PurchaseAgg pa 
    LEFT JOIN purchase_prices pp 
        ON pa.Brand = pp.Brand 
    LEFT JOIN SalesSummary ss 
        ON pa.VendorNumber = ss.VendorNo 
        AND pa.Brand = ss.Brand 
    LEFT JOIN FreightSummary fs 
        ON pa.VendorNumber = fs.VendorNumber 
    ORDER BY pa.TotalPurchaseDollars DESC
    """
    
    vendor_sales_summary = pd.read_sql_query(query, conn)
    
    # Creating new columns for better analysis
    vendor_sales_summary['GrossProfit'] = vendor_sales_summary['TotalSalesDollars'] - vendor_sales_summary['TotalPurchaseDollars']
    vendor_sales_summary['ProfitMargin'] = ((vendor_sales_summary['GrossProfit'] / vendor_sales_summary['TotalSalesDollars']) * 100).round(2)
    vendor_sales_summary['StockTurnover'] = vendor_sales_summary['TotalSalesQuantity'] / vendor_sales_summary['TotalPurchaseQuantity']
    vendor_sales_summary['SalestoPurchaseRatio'] = vendor_sales_summary['TotalSalesDollars'] / vendor_sales_summary['TotalPurchaseDollars']
    
    return vendor_sales_summary

def clean_data(df):
    '''Handles data type conversions, null values, and string formatting.'''
    
    # Convert Volume column to float64
    df['Volume'] = df['Volume'].astype('float64')
    
    # Fill any null values (like missing sales data) with 0
    df.fillna(0, inplace=True)
    
    # Strip trailing spaces from VendorName
    df['VendorName'] = df['VendorName'].str.strip()
    
    return df

if __name__ == '__main__':
    # Record the start time of the entire script
    start_time = datetime.now()
    t0 = time.time()
    
    logging.info(f"==================================================")
    logging.info(f"Data Ingestion Script Started at: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"==================================================")
    
    # 1. Create database connection
    conn = sqlite3.connect('inventory.db')
    
    try:
        # 2. Optimize Database (Create Indexes before running the big query)
        logging.info('Optimizing database structures.....')
        optimize_database(conn)
        
        # 3. Extract and Transform
        logging.info('Creating Vendor Summary Table.....')
        summary_df = create_vendor_summary(conn)
        
        # 4. Clean Data
        logging.info('Cleaning Data.....')
        clean_df = clean_data(summary_df)
        logging.info(f"Sample of processed data:\n{clean_df.head()}")
        
        # 5. Load (Ingest) Data
        logging.info('Ingesting data into SQLite database.....')
        ingest_db(clean_df, 'vendor_sales_summary', conn)
        
        # Record the end time and calculate duration
        end_time = datetime.now()
        t1 = time.time()
        duration = round(t1 - t0, 2)
        
        logging.info(f"==================================================")
        logging.info(f"Data Ingestion Script Ended at: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Total time taken: {duration} seconds")
        logging.info(f"==================================================")
        
    except Exception as e:
        logging.error(f"An error occurred: {e}")
        
    finally:
        # Always ensure the database connection closes safely
        conn.close()