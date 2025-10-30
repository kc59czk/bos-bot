import csv
from datetime import datetime
from collections import defaultdict
from decimal import Decimal


def parse_amount(amount_str):
    """Convert amount string with comma to Decimal"""
    return Decimal(amount_str.replace(',', '.'))


def parse_dividend_file(file_path):
    """Parse the dividend CSV file and return summarized data by stock"""
    dividends_by_stock = defaultdict(list)
    
    # Try different encodings that are common for Polish text
    encodings = ['utf-8', 'cp1250', 'iso-8859-2', 'cp852']
    last_error = None

    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as file:
                # Skip the header
                next(file)

                reader = csv.reader(file, delimiter=';')
                for row in reader:
                    if len(row) >= 4:  # Ensure we have all needed columns
                        date_str, operation, details, amount = row

                        # Extract stock name from operation title
                        if 'Wyp' in operation and 'dywidendy' in operation:
                            stock_name = operation.split()[-1]

                            # Parse date and amount
                            date = datetime.strptime(date_str, '%Y-%m-%d')
                            amount = parse_amount(amount)

                            # Store dividend information including source file name (populated by caller)
                            dividends_by_stock[stock_name].append({
                                'date': date,
                                'amount': amount
                            })
                # If we got here, the file was read successfully
                return dividends_by_stock
        except UnicodeDecodeError as e:
            last_error = e
            continue
        except Exception as e:
            print(f"Error processing file with {encoding} encoding: {e}")
            raise

    # If we get here, none of the encodings worked
    if last_error:
        print("Failed to decode the file with any of the attempted encodings.")
        raise last_error

    return dividends_by_stock


def print_dividend_summary(dividends_by_stock):
    """Print summary of dividends for each stock"""
    print("\nDividend Summary by Stock:")
    print("-" * 50)
    
    grand_total = Decimal('0')
    
    for stock, payments in sorted(dividends_by_stock.items()):
        total = sum(payment['amount'] for payment in payments)
        grand_total += total
        
        print(f"\n{stock}:")
        print(f"Number of payments: {len(payments)}")
        print(f"Total amount: {total:,.2f} PLN")
        
        # Show individual payments
        print("Payment dates and amounts:")
        for payment in sorted(payments, key=lambda x: x['date'], reverse=True):
            source = payment.get('source')
            source_str = f" ({source})" if source else ""
            print(
                f"  {payment['date'].strftime('%Y-%m-%d')}: "
                f"{payment['amount']:,.2f} PLN"
                f"{source_str}"
            )
    
    print("\n" + "=" * 50)
    print(f"Total dividends across all stocks: {grand_total:,.2f} PLN")


if __name__ == '__main__':
    import os
    import glob

    input_dir = 'input'
    try:
        # Collect all CSV files inside input/ (non-recursive)
        pattern = os.path.join(input_dir, '*.csv')
        files = glob.glob(pattern)

        if not files:
            raise FileNotFoundError(f"No CSV files found in '{input_dir}'")

        # Aggregate results from all files
        aggregated = defaultdict(list)
        for fpath in files:
            fname = os.path.basename(fpath)
            source_name, _ = os.path.splitext(fname)

            try:
                result = parse_dividend_file(fpath)
            except FileNotFoundError:
                print(f"Skipping missing file: {fpath}")
                continue

            # Merge and annotate payments with source file name
            for stock, payments in result.items():
                for p in payments:
                    p['source'] = source_name
                    aggregated[stock].append(p)

        print_dividend_summary(aggregated)
    except FileNotFoundError as fnf:
        print(f"Error: {fnf}")
    except Exception as e:
        print(f"Error processing files: {e}")
