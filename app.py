import random
from flask import Flask, request, jsonify, g
import redis
import sqlite3
import json
import time
import threading
import logging
from functools import wraps
import os
import queue

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

try:
    redis_client.ping()
    logger.info("Successfully connected to Redis.")
except redis.exceptions.ConnectionError as e:
    logger.error(f"Could not connect to Redis: {e}")

DB_NAME = 'products.db'

def get_db():
    """Opens a new database connection if there is none yet for the current application context."""
    if 'db' not in g:
        try:
            g.db = sqlite3.connect(DB_NAME)
            g.db.row_factory = sqlite3.Row
            logger.debug("Database connection opened.")
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database {DB_NAME}: {e}")
            raise
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Closes the database again at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()
        logger.debug("Database connection closed.")
    if error:
         logger.error(f"Application context teardown with error: {error}")

def init_db():
    """Initialize the SQLite database"""
    if os.path.exists(DB_NAME):
         logger.warning(f"Removing existing database: {DB_NAME}")
         try:
              os.remove(DB_NAME)
         except OSError as e:
              logger.error(f"Error removing database file {DB_NAME}: {e}")
              return

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            description TEXT
        )
        ''')
        logger.info("Database initialized")
    except sqlite3.Error as e:
        logger.error(f"Database initialization failed: {e}")
        if os.path.exists(DB_NAME):
            try:
                os.remove(DB_NAME)
            except OSError:
                pass


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.cache_hits = 0
            self.cache_misses = 0
            self.db_reads = 0
            self.db_writes = 0
            self.operation_times = {
                "cache_aside": {"read": [], "write": []},
                "read_through": {"read": [], "write": []},
                "write_through": {"read": [], "write": []},
                "write_around": {"read": [], "write": []},
                "write_back": {"read": [], "write": []}
            }

    def record_cache_hit(self):
        with self._lock:
            self.cache_hits += 1

    def record_cache_miss(self):
        with self._lock:
            self.cache_misses += 1

    def record_db_read(self):
        with self._lock:
            self.db_reads += 1

    def record_db_write(self):
        with self._lock:
            self.db_writes += 1

    def record_time(self, strategy, operation, elapsed_time):
         with self._lock:
             if strategy not in self.operation_times:
                 self.operation_times[strategy] = {"read": [], "write": []}
             if operation not in self.operation_times[strategy]:
                  self.operation_times[strategy][operation] = []

             self.operation_times[strategy][operation].append(elapsed_time)

    def get_stats(self):
        with self._lock:
            hit_rate = 0
            total_lookups = self.cache_hits + self.cache_misses
            if total_lookups > 0:
                hit_rate = self.cache_hits / total_lookups * 100

            avg_times = {}
            for strategy, ops in self.operation_times.items():
                avg_times[strategy] = {
                    "read": sum(ops["read"]) / len(ops["read"]) if ops["read"] else 0,
                    "write": sum(ops["write"]) / len(ops["write"]) if ops["write"] else 0
                }

            return {
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "hit_rate": hit_rate,
                "db_reads": self.db_reads,
                "db_writes": self.db_writes,
                "avg_operation_times": avg_times
            }

metrics = Metrics()

def timer(strategy, operation):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.monotonic()
            result = func(*args, **kwargs)
            elapsed_time = time.monotonic() - start_time
            metrics.record_time(strategy, operation, elapsed_time)
            return result
        return wrapper
    return decorator

def get_from_db(product_id):
    """Get a product from the database using the request context connection."""
    metrics.record_db_read()
    try:
        cursor = get_db().cursor()
        cursor.execute('SELECT * FROM products WHERE id = ?', (product_id,))
        product = cursor.fetchone()
        if product:
            return dict(product)
        return None
    except sqlite3.Error as e:
        logger.error(f"Error getting product {product_id} from DB: {e}")
        return None

def save_to_db(product_data):
    """Save product to the database using the request context connection."""
    metrics.record_db_write()
    product_id = product_data.get('id')
    name = product_data.get('name')
    price = product_data.get('price')
    description = product_data.get('description')

    if product_id is None or name is None or price is None:
         logger.error(f"Attempted to save invalid product data: {product_data}")
         raise ValueError("Invalid product data for saving to DB")

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT 1 FROM products WHERE id = ?', (product_id,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute(
                'UPDATE products SET name = ?, price = ?, description = ? WHERE id = ?',
                (name, price, description, product_id)
            )
            logger.debug(f"Updated product {product_id} in DB.")
        else:
            cursor.execute(
                'INSERT INTO products (id, name, price, description) VALUES (?, ?, ?, ?)',
                (product_id, name, price, description)
            )
            logger.debug(f"Inserted product {product_id} into DB.")

        conn.commit()
        return product_data
    except sqlite3.Error as e:
        logger.error(f"Error saving product {product_id} to DB: {e}")
        conn.rollback()
        raise

def get_from_cache(product_id):
    """Get a product from Redis cache"""
    cache_key = f"product:{product_id}"
    try:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            metrics.record_cache_hit()
            return json.loads(cached_data)
        else:
            metrics.record_cache_miss()
            return None
    except redis.exceptions.RedisError as e:
        logger.error(f"Redis GET error for key {cache_key}: {e}")
        metrics.record_cache_miss()
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from cache key {cache_key}: {e}")
        invalidate_cache(product_id)
        metrics.record_cache_miss()
        return None


def save_to_cache(product_data, expiry=3600):
    """Save product to Redis cache"""
    if not product_data or 'id' not in product_data:
        logger.warning("Attempted to save invalid data to cache.")
        return None

    product_id = product_data.get('id')
    cache_key = f"product:{product_id}"
    try:
        redis_client.setex(cache_key, expiry, json.dumps(product_data))
        logger.debug(f"Saved product {product_id} to cache with expiry {expiry}s.")
        return product_data
    except redis.exceptions.RedisError as e:
        logger.error(f"Redis SETEX error for key {cache_key}: {e}")
        return None

def invalidate_cache(product_id):
    """Remove a product from the cache"""
    cache_key = f"product:{product_id}"
    try:
        deleted_count = redis_client.delete(cache_key)
        logger.debug(f"Invalidated cache key {cache_key}. Deleted: {deleted_count > 0}")
        return deleted_count > 0
    except redis.exceptions.RedisError as e:
        logger.error(f"Redis DELETE error for key {cache_key}: {e}")
        return False

@timer("cache_aside", "read")
def cache_aside_read(product_id):
    """Cache-Aside read strategy"""
    product = get_from_cache(product_id)
    if product:
        return product

    logger.info(f"[Cache-Aside] Cache miss for product {product_id}. Reading from DB.")
    try:
        product = get_from_db(product_id)
        if product:
            logger.info(f"[Cache-Aside] Found product {product_id} in DB. Updating cache.")
            save_to_cache(product)
    except Exception as e:
         logger.error(f"[Cache-Aside] Error during DB read for product {product_id}: {e}")

    return product

@timer("cache_aside", "write")
def cache_aside_write(product_data):
    """Cache-Aside write strategy"""
    try:
        save_to_db(product_data)
        logger.info(f"[Cache-Aside] Saved product {product_data.get('id')} to DB.")
        invalidated = invalidate_cache(product_data.get('id'))
        if invalidated:
             logger.info(f"[Cache-Aside] Invalidated cache for product {product_data.get('id')}.")
    except Exception as e:
         logger.error(f"[Cache-Aside] Error during write for product {product_data.get('id')}: {e}")
         raise
    return product_data

@timer("read_through", "read")
def read_through_read(product_id):
    """Read-Through read strategy (Simulated at application level).
    Functionally identical to Cache-Aside read in this implementation.
    A true Read-Through involves the cache provider handling the DB fetch.
    """
    product = get_from_cache(product_id)
    if product:
        return product

    logger.info(f"[Read-Through] Cache miss for product {product_id}. Reading from DB (simulated).")
    try:
        product = get_from_db(product_id)
        if product:
            logger.info(f"[Read-Through] Found product {product_id} in DB. Updating cache.")
            save_to_cache(product)
    except Exception as e:
        logger.error(f"[Read-Through] Error during DB read for product {product_id}: {e}")

    return product

@timer("read_through", "write")
def read_through_write(product_data):
    """Read-Through write strategy (Often same as Cache-Aside write: Write-To-DB, Invalidate-Cache)."""
    try:
        save_to_db(product_data)
        logger.info(f"[Read-Through] Saved product {product_data.get('id')} to DB.")
        invalidated = invalidate_cache(product_data.get('id'))
        if invalidated:
             logger.info(f"[Read-Through] Invalidated cache for product {product_data.get('id')}.")
    except Exception as e:
         logger.error(f"[Read-Through] Error during write for product {product_data.get('id')}: {e}")
         raise
    return product_data

@timer("write_through", "read")
def write_through_read(product_id):
    """Write-Through read strategy (Same read logic as Cache-Aside)."""
    return cache_aside_read(product_id)

@timer("write_through", "write")
def write_through_write(product_data):
    """Write-Through write strategy: Write to DB and Cache synchronously."""
    cache_updated = False
    try:
        save_to_db(product_data)
        logger.info(f"[Write-Through] Saved product {product_data.get('id')} to DB.")
        saved_cache = save_to_cache(product_data)
        if saved_cache:
            logger.info(f"[Write-Through] Updated cache for product {product_data.get('id')}.")
            cache_updated = True
        else:
             logger.warning(f"[Write-Through] Failed to update cache for product {product_data.get('id')} after DB write.")
    except Exception as e:
         logger.error(f"[Write-Through] Error during write for product {product_data.get('id')}: {e}")
         raise
    return product_data

@timer("write_around", "read")
def write_around_read(product_id):
    """Write-Around read strategy (Same read logic as Cache-Aside)."""
    return cache_aside_read(product_id)

@timer("write_around", "write")
def write_around_write(product_data):
    """Write-Around write strategy: Write directly to DB, bypass cache."""
    try:
        save_to_db(product_data)
        logger.info(f"[Write-Around] Saved product {product_data.get('id')} directly to DB (bypassed cache).")
    except Exception as e:
        logger.error(f"[Write-Around] Error during write for product {product_data.get('id')}: {e}")
        raise
    return product_data


write_back_queue = queue.Queue()
write_back_thread = None
write_back_running = False

def process_write_back_queue():
    """Process the write-back queue, writing items to DB. Runs in a separate thread."""
    logger.info("Write-back thread started processing.")
    while write_back_running or not write_back_queue.empty():
        try:
            product_data = write_back_queue.get(block=True, timeout=1)

            try:
                conn_wb = sqlite3.connect(DB_NAME)
                conn_wb.row_factory = sqlite3.Row
                metrics.record_db_write()
                product_id = product_data.get('id')
                name = product_data.get('name')
                price = product_data.get('price')
                description = product_data.get('description')

                if product_id is None or name is None or price is None:
                    logger.error(f"[Write-Back Thread] Invalid product data received: {product_data}")
                    write_back_queue.task_done()
                    continue

                cursor = conn_wb.cursor()
                cursor.execute('SELECT 1 FROM products WHERE id = ?', (product_id,))
                exists = cursor.fetchone()
                if exists:
                    cursor.execute('UPDATE products SET name = ?, price = ?, description = ? WHERE id = ?',
                                   (name, price, description, product_id))
                else:
                    cursor.execute('INSERT INTO products (id, name, price, description) VALUES (?, ?, ?, ?)',
                                   (product_id, name, price, description))
                conn_wb.commit()
                conn_wb.close()
                logger.info(f"[Write-Back Thread] Successfully wrote product {product_id} to DB.")

            except sqlite3.Error as e:
                logger.error(f"[Write-Back Thread] DB error saving product {product_id}: {e}")
                # conn_wb.rollback()
                if 'conn_wb' in locals() and conn_wb:
                    conn_wb.close()
            except Exception as e:
                logger.error(f"[Write-Back Thread] Unexpected error processing product {product_id}: {e}")
                if 'conn_wb' in locals() and conn_wb:
                    conn_wb.close()

            finally:
                 write_back_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
             logger.error(f"[Write-Back Thread] Error getting item from queue: {e}")
             time.sleep(1)

    logger.info("Write-back thread finished processing.")


def start_write_back_thread():
    """Start the write-back processing thread"""
    global write_back_running, write_back_thread
    if not write_back_running and (write_back_thread is None or not write_back_thread.is_alive()):
        write_back_running = True
        write_back_thread = threading.Thread(target=process_write_back_queue, name="WriteBackThread")
        write_back_thread.daemon = True
        write_back_thread.start()
        logger.info("Write-back processing thread started.")
    elif write_back_running:
         logger.warning("Write-back thread already signaled to run.")
    elif write_back_thread and write_back_thread.is_alive():
         logger.warning("Write-back thread is already alive but running flag was False. Resetting flag.")
         write_back_running = True


def stop_write_back_thread():
    """Signal the write-back processing thread to stop and wait for it."""
    global write_back_running, write_back_thread
    if not write_back_running and (write_back_thread is None or not write_back_thread.is_alive()):
        logger.info("Write-back thread is not running or doesn't exist.")
        return

    if write_back_running:
        logger.info("Signaling write-back thread to stop...")
        write_back_running = False

        logger.info("Waiting for write-back queue to empty...")
        write_back_queue.join()
        logger.info("Write-back queue is empty.")

        if write_back_thread and write_back_thread.is_alive():
            logger.info("Waiting for write-back thread to terminate...")
            write_back_thread.join(timeout=10.0)
            if write_back_thread.is_alive():
                logger.warning("Write-back thread did not terminate gracefully within timeout.")
            else:
                logger.info("Write-back thread stopped successfully.")
        else:
             logger.info("Write-back thread was not alive.")

        write_back_thread = None
    else:
         logger.info("Write-back thread stop requested, but it was already signaled to stop.")


@timer("write_back", "read")
def write_back_read(product_id):
    """Write-Back read strategy (Same read logic as Cache-Aside)."""
    return cache_aside_read(product_id)

@timer("write_back", "write")
def write_back_write(product_data):
    """Write-Back write strategy: Update cache, queue DB write for later."""
    saved_cache = save_to_cache(product_data)
    if saved_cache:
        logger.info(f"[Write-Back] Updated cache for product {product_data.get('id')}.")
        try:
            write_back_queue.put(product_data)
            logger.info(f"[Write-Back] Queued product {product_data.get('id')} for DB write.")
        except Exception as e:
             logger.error(f"[Write-Back] Failed to queue product {product_data.get('id')} for DB write: {e}")
             invalidate_cache(product_data.get('id'))
             raise
    else:
        logger.error(f"[Write-Back] Failed to update cache for product {product_data.get('id')}. Aborting DB queue.")
        raise IOError(f"Failed to save product {product_data.get('id')} to cache in Write-Back.")

    return product_data


@app.route('/api/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    strategy = request.args.get('strategy', 'cache_aside')
    strategies = {
        'cache_aside': cache_aside_read,
        'read_through': read_through_read, # Simulates read-through only
        'write_through': write_through_read,
        'write_around': write_around_read,
        'write_back': write_back_read
    }

    if strategy not in strategies:
        return jsonify({'error': 'Invalid caching strategy'}), 400

    logger.info(f"GET /api/products/{product_id} using strategy: {strategy}")
    try:
        product = strategies[strategy](product_id)
        if product:
            return jsonify(product)
        else:
            return jsonify({'error': 'Product not found'}), 404
    except Exception as e:
         logger.error(f"Error in GET /api/products/{product_id} with strategy {strategy}: {e}", exc_info=True)
         return jsonify({'error': 'An internal server error occurred'}), 500


@app.route('/api/products', methods=['POST'])
def create_or_update_product():
    product_data = request.json
    strategy = request.args.get('strategy', 'cache_aside')

    if not product_data or 'id' not in product_data or 'name' not in product_data or 'price' not in product_data:
        return jsonify({'error': 'Invalid or incomplete product data. Requires id, name, price.'}), 400

    try:
        product_data['id'] = int(product_data['id'])
    except (ValueError, TypeError):
         return jsonify({'error': 'Product ID must be an integer.'}), 400

    strategies = {
        'cache_aside': cache_aside_write,
        'read_through': read_through_write, # Simulates read-through write
        'write_through': write_through_write,
        'write_around': write_around_write,
        'write_back': write_back_write
    }

    if strategy not in strategies:
        return jsonify({'error': 'Invalid caching strategy'}), 400

    logger.info(f"POST /api/products using strategy: {strategy} for product ID: {product_data.get('id')}")

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM products WHERE id = ?', (product_data['id'],))
        exists_before = cursor.fetchone()

        saved_product = strategies[strategy](product_data)

        status_code = 200 if exists_before else 201
        return jsonify(saved_product), status_code
    except ValueError as e:
         logger.warning(f"Validation error during POST /api/products: {e}")
         return jsonify({'error': str(e)}), 400
    except Exception as e:
         logger.error(f"Error in POST /api/products with strategy {strategy} for product {product_data.get('id')}: {e}", exc_info=True)
         return jsonify({'error': 'An internal server error occurred'}), 500


@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    return jsonify(metrics.get_stats())

@app.route('/api/metrics/reset', methods=['POST'])
def reset_metrics_endpoint():
    logger.info("Resetting metrics via API call.")
    metrics.reset()
    # redis_client.flushall()
    # init_db()
    return jsonify({'message': 'Metrics reset successfully'})

def run_simulation(strategy, num_reads, num_writes, prepopulate=False):
    """Runs a simulation for a given strategy with reads/writes intercalés aléatoirement."""
    read_func, write_func = {
        'cache_aside': (cache_aside_read, cache_aside_write),
        'read_through': (read_through_read, read_through_write),
        'write_through': (write_through_read, write_through_write),
        'write_around': (write_around_read, write_around_write),
        'write_back': (write_back_read, write_back_write)
    }.get(strategy)

    if not read_func or not write_func:
        raise ValueError(f"Invalid strategy provided to run_simulation: {strategy}")

    write_jobs = []
    for i in range(num_writes):
        product_id = i + 1
        write_jobs.append({
            'id': product_id,
            'name': f'Simulated Product {product_id}',
            'price': round(100.0 + (product_id * 10) + (i * 0.1), 2),
            'description': f'Simulated description update {i+1} for product {product_id}'
        })
        
    if prepopulate:
        for job in write_jobs:
            save_to_db(job)
            save_to_cache(job) 

    logging.info(f"--- Starting simulation for strategy: {strategy} ---")
    metrics.reset()

    ops = ['w'] * num_writes + ['r'] * num_reads
    random.shuffle(ops)

    successful_reads = 0
    successful_writes = 0

    for op in ops:
        if op == 'w':
            data = write_jobs.pop(0)
            try:
                write_func(data)
                successful_writes += 1
            except Exception as e:
                logging.error(f"Simulation write error ({strategy}, ID {data['id']}): {e}")
        else:
            product_id = random.randint(1, num_writes)
            try:
                read_func(product_id)
                successful_reads += 1
            except Exception as e:
                logging.error(f"Simulation read error ({strategy}, ID {product_id}): {e}")

    if strategy == 'write_back':
        logging.info("[Simulation] Allowing write-back queue time to process...")
        time.sleep(5)

    logging.info(
        f"--- Finished simulation for strategy: {strategy} "
        f"(Reads: {successful_reads}/{num_reads}, Writes: {successful_writes}/{num_writes}) ---"
    )
    return metrics.get_stats()


@app.route('/api/simulate', methods=['POST'])
def simulate_operations_endpoint():
    """Simulate read and write operations with a specific strategy"""
    data = request.json
    num_reads = int(data.get('reads', 100))
    num_writes = int(data.get('writes', 20))
    strategy = data.get('strategy')

    if not strategy:
        return jsonify({'error': 'Missing "strategy" parameter'}), 400

    valid_strategies = ['cache_aside', 'read_through', 'write_through', 'write_around', 'write_back']
    if strategy not in valid_strategies:
        return jsonify({'error': f'Invalid caching strategy. Choose from: {valid_strategies}'}), 400

    try:
        simulation_metrics = run_simulation(strategy, num_reads, num_writes, prepopulate=False)
        return jsonify({
            'strategy': strategy,
            'requested_reads': num_reads,
            'requested_writes': num_writes,
            'metrics': simulation_metrics
        })
    except ValueError as e:
         return jsonify({'error': str(e)}), 400
    except Exception as e:
         logger.error(f"Error during simulation endpoint for strategy {strategy}: {e}", exc_info=True)
         return jsonify({'error': 'An internal server error occurred during simulation'}), 500


@app.route('/api/compare', methods=['POST'])
def compare_strategies_endpoint():
    """Compare all caching strategies with the same workload"""
    data = request.json
    num_reads = int(data.get('reads', 100))
    num_writes = int(data.get('writes', 20))
    reset_db_each_run = bool(data.get('reset_db', True))

    results = {}
    strategies_to_compare = ['cache_aside', 'read_through', 'write_through', 'write_around', 'write_back']

    with app.app_context():
        for strategy in strategies_to_compare:
            logger.info(f"--- Comparing Strategy: {strategy} ---")

            if reset_db_each_run:
                logger.info(f"Resetting database for strategy {strategy}...")
                init_db()

            try:
                 redis_client.flushdb()
                 logger.info(f"Cleared Redis DB for strategy {strategy}.")
            except redis.exceptions.RedisError as e:
                 logger.error(f"Failed to clear Redis DB for strategy {strategy}: {e}")
                 results[strategy] = {"error": "Failed to clear Redis cache"}
                 continue

            try:
                simulation_metrics = run_simulation(strategy, num_reads, num_writes, prepopulate=False)
                results[strategy] = {
                    'requested_reads': num_reads,
                    'requested_writes': num_writes,
                    'metrics': simulation_metrics
                }
            except ValueError as e:
                logger.error(f"Value error during comparison for strategy {strategy}: {e}")
                results[strategy] = {"error": str(e)}
            except Exception as e:
                logger.error(f"Error during comparison simulation for strategy {strategy}: {e}", exc_info=True)
                results[strategy] = {"error": "Simulation failed"}

            time.sleep(1)

    return jsonify(results)


@app.route('/')
def index():
    return """
    <html>
        <head>
            <title>Caching Strategies Demo</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; background-color: #f4f4f4; color: #333; }
                h1, h2 { color: #0056b3; }
                ul { list-style-type: disc; margin-left: 20px; }
                li { margin-bottom: 5px; }
                code { background-color: #e9e9e9; padding: 2px 5px; border-radius: 3px; font-family: Consolas, Monaco, monospace; }
                pre { background-color: #333; color: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto; }
                .endpoint { color: #006400; }
                .method { font-weight: bold; color: #d9534f; } /* Example: Red for POST */
                .get { font-weight: bold; color: #5cb85c; } /* Example: Green for GET */
            </style>
        </head>
        <body>
            <h1>Caching Strategies Demo</h1>
            <p>This application demonstrates different caching strategies using Flask, Redis, and SQLite.</p>

            <h2>Available Endpoints:</h2>
            <ul>
                <li><span class="get">GET</span> <code class="endpoint">/api/products/{id}?strategy={strategy}</code> - Retrieve a product</li>
                <li><span class="method">POST</span> <code class="endpoint">/api/products?strategy={strategy}</code> - Create/update a product (Payload: <code>{"id": N, "name": "...", "price": ..., "description": "..."}</code>)</li>
                <li><span class="get">GET</span> <code class="endpoint">/api/metrics</code> - Get current performance metrics</li>
                <li><span class="method">POST</span> <code class="endpoint">/api/metrics/reset</code> - Reset performance metrics</li>
                <li><span class="method">POST</span> <code class="endpoint">/api/simulate</code> - Simulate operations for a specific strategy (Payload: <code>{"strategy": "...", "reads": N, "writes": M}</code>)</li>
                <li><span class="method">POST</span> <code class="endpoint">/api/compare</code> - Compare all strategies (Payload: <code>{"reads": N, "writes": M, "reset_db": true/false}</code>)</li>
            </ul>

            <h2>Available Strategies:</h2>
            <ul>
                <li><code>cache_aside</code></li>
                <li><code>read_through</code> (Simulated)</li>
                <li><code>write_through</code></li>
                <li><code>write_around</code></li>
                <li><code>write_back</code></li>
            </ul>

            <h2>Example Usage (using cURL):</h2>
            <pre>
# Get product 1 using cache-aside
curl "http://localhost:5000/api/products/1?strategy=cache_aside"

# Create/Update product 6 using write-through
curl -X POST "http://localhost:5000/api/products?strategy=write_through" \
 -H "Content-Type: application/json" \
 -d '{"id": 6, "name": "New Widget", "price": 59.95, "description": "A brand new widget type"}'

# Simulate 200 reads and 50 writes with write-back
curl -X POST "http://localhost:5000/api/simulate" \
 -H "Content-Type: application/json" \
 -d '{"strategy": "write_back", "reads": 200, "writes": 50}'

# Compare all strategies with 500 reads, 100 writes, resetting DB each time
curl -X POST "http://localhost:5000/api/compare" \
 -H "Content-Type: application/json" \
 -d '{"reads": 500, "writes": 100, "reset_db": true}'

# Get current metrics
curl http://localhost:5000/api/metrics

# Reset metrics
curl -X POST http://localhost:5000/api/metrics/reset
            </pre>
        </body>
    </html>
    """

if __name__ == '__main__':
    with app.app_context():
         init_db()
    start_write_back_thread()
    try:
        app.run(host='0.0.0.0', port=5000, debug=True)
    except KeyboardInterrupt:
         logger.info("Shutdown signal received.")
    finally:
        stop_write_back_thread()
        logger.info("Application shut down.")