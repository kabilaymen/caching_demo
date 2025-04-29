# Caching Strategies Demo

This project demonstrates five different caching strategies using Redis as the caching layer and SQLite as the database.

## Prerequisites

- Python 3.7+
- Redis server
- SQLite

## Installation

1. Install the required Python packages:

```bash
pip install flask redis matplotlib numpy requests
```

2. Start your Redis server:

```bash
redis-server
```

## Running the Application

1. Start the Flask application:

```bash
python app.py
```

2. Run the simulation script to test and compare caching strategies:

```bash
python simulation.py
```

## Caching Strategies Implemented

### 1. Cache-Aside

The application first checks the cache for data. If not found (cache miss), it fetches from the database and updates the cache.

**Flow:**
- **Read:** Check cache → If miss, read from DB → Update cache
- **Write:** Update DB → Invalidate cache entry

**Pros:**
- Cache only contains requested data
- Simple to implement
- Application has control over cache population

**Cons:**
- Initial reads have higher latency (cold cache)
- Extra code complexity in application layer

### 2. Read-Through

Similar to Cache-Aside, but the cache system is responsible for loading data from the database on a miss.

**Flow:**
- **Read:** Request from cache → Cache automatically fetches from DB if needed
- **Write:** Similar to Cache-Aside, write to DB and invalidate cache

**Pros:**
- Simplifies application code (cache management delegated to cache layer)
- Cache only contains requested data

**Cons:**
- Initial reads have higher latency (cold cache)
- May require specialized caching library

### 3. Write-Through

Data is written to both the cache and database simultaneously.

**Flow:**
- **Read:** Check cache → If miss, read from DB → Update cache
- **Write:** Update DB AND cache simultaneously

**Pros:**
- Cache always consistent with database
- Good read performance
- Simple recovery model

**Cons:**
- Higher write latency
- Cache contains potentially unused data

### 4. Write-Around

Data is written directly to the database, bypassing the cache.

**Flow:**
- **Read:** Check cache → If miss, read from DB → Update cache
- **Write:** Write directly to DB, bypass cache

**Pros:**
- Good for write-heavy workloads
- Prevents cache pollution with write-only data
- Lower write latency

**Cons:**
- Read latency for recently written data (cache misses)
- Cache can become stale

### 5. Write-Back (Write-Behind)

Data is written to cache first, then asynchronously flushed to database.

**Flow:**
- **Read:** Check cache → If miss, read from DB → Update cache
- **Write:** Write to cache → Asynchronously write to DB later

**Pros:**
- Fastest write performance
- Great for write-heavy workloads
- Reduces database load (batching)

**Cons:**
- Risk of data loss if system fails before flush
- More complex implementation
- Eventual consistency only

## API Endpoints

- `GET /api/products/{id}?strategy={strategy}` - Retrieve a product
- `POST /api/products?strategy={strategy}` - Create/update a product
- `GET /api/metrics` - Get performance metrics
- `POST /api/metrics/reset` - Reset metrics
- `POST /api/simulate` - Simulate operations for a strategy
- `POST /api/compare` - Compare all strategies

## Example Usage

```bash
# Get a product using cache-aside strategy
curl http://localhost:5000/api/products/1?strategy=cache_aside

# Create a product using write-through strategy
curl -X POST http://localhost:5000/api/products?strategy=write_through \
  -H "Content-Type: application/json" \
  -d '{"id": 6, "name": "New Product", "price": 49.99, "description": "A new product"}'

# Simulate operations
curl -X POST http://localhost:5000/api/simulate \
  -H "Content-Type: application/json" \
  -d '{"strategy": "cache_aside", "reads": 100, "writes": 20}'

# Compare all strategies
curl -X POST http://localhost:5000/api/compare \
  -H "Content-Type: application/json" \
  -d '{"reads": 100, "writes": 20}'
```

## Analysis and Results

The simulation script generates two graphical comparisons:

1. **hit_rates.png** - Compares the cache hit rates for each strategy
2. **operation_times.png** - Compares average read and write times for each strategy

The performance analysis outputs:
- Hit rates comparison
- Read/write times comparison
- Strategy-specific analysis with pros and cons

## Conclusion

Different caching strategies offer different trade-offs between:
- Read performance
- Write performance
- Data consistency
- Risk of data loss
- Implementation complexity

Choose the appropriate strategy based on your application's specific requirements:
- **Cache-Aside**: Good general-purpose strategy
- **Read-Through**: When you want to abstract cache management
- **Write-Through**: When consistency is critical
- **Write-Around**: For write-heavy workloads with infrequent reads of new data
- **Write-Back**: For write-heavy workloads with tolerance for eventual consistency