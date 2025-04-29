import requests
import time
import matplotlib.pyplot as plt
import numpy as np

BASE_URL = "http://localhost:5000/api"

def run_simulation(strategy, reads=1000, writes=200):
    """Run a simulation for a specific caching strategy"""
    print(f"Running simulation for strategy: {strategy}")
    
    requests.post(f"{BASE_URL}/metrics/reset")
    
    response = requests.post(
        f"{BASE_URL}/simulate",
        json={"strategy": strategy, "reads": reads, "writes": writes}
    )
    
    return response.json()

def compare_all_strategies(reads=1000, writes=200):
    """Compare all caching strategies"""
    response = requests.post(
        f"{BASE_URL}/compare",
        json={"reads": reads, "writes": writes}
    )
    
    return response.json()

def plot_hit_rates(results):
    """Plot cache hit rates for all strategies"""
    strategies = list(results.keys())
    hit_rates = [results[s]["metrics"]["hit_rate"] for s in strategies]
    
    plt.figure(figsize=(10, 6))
    plt.bar(strategies, hit_rates)
    plt.title("Cache Hit Rates by Strategy")
    plt.xlabel("Strategy")
    plt.ylabel("Hit Rate (%)")
    plt.ylim(0, 100)
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig("hit_rates.png")
    
def plot_operation_times(results):
    """Plot average operation times for all strategies"""
    strategies = list(results.keys())
    read_times = [results[s]["metrics"]["avg_operation_times"][s]["read"] * 1000 for s in strategies]
    write_times = [results[s]["metrics"]["avg_operation_times"][s]["write"] * 1000 for s in strategies]
    
    x = np.arange(len(strategies))
    width = 0.35
    
    fig, ax = plt.subplots(figsize=(12, 7))
    rects1 = ax.bar(x - width/2, read_times, width, label='Read (ms)')
    rects2 = ax.bar(x + width/2, write_times, width, label='Write (ms)')
    
    ax.set_title('Average Operation Times by Strategy')
    ax.set_xlabel('Strategy')
    ax.set_ylabel('Time (ms)')
    ax.set_xticks(x)
    ax.set_xticklabels(strategies, rotation=45)
    ax.legend()
    
    def autolabel(rects):
        for rect in rects:
            height = rect.get_height()
            ax.annotate(f'{height:.2f}',
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3),
                        textcoords="offset points",
                        ha='center', va='bottom')
    
    autolabel(rects1)
    autolabel(rects2)
    
    fig.tight_layout()
    plt.savefig("operation_times.png")

def print_performance_analysis(results):
    """Print performance analysis of different strategies"""
    print("\n===== PERFORMANCE ANALYSIS =====")
    
    hit_rates = [(s, results[s]["metrics"]["hit_rate"]) for s in results.keys()]
    hit_rates.sort(key=lambda x: x[1], reverse=True)
    
    print("\nCache Hit Rates (highest to lowest):")
    for strategy, hit_rate in hit_rates:
        print(f"  {strategy}: {hit_rate:.2f}%")
    
    read_times = [(s, results[s]["metrics"]["avg_operation_times"][s]["read"] * 1000) for s in results.keys()]
    read_times.sort(key=lambda x: x[1])
    
    print("\nRead Times (fastest to slowest):")
    for strategy, time_ms in read_times:
        print(f"  {strategy}: {time_ms:.2f} ms")
    
    write_times = [(s, results[s]["metrics"]["avg_operation_times"][s]["write"] * 1000) for s in results.keys()]
    write_times.sort(key=lambda x: x[1])
    
    print("\nWrite Times (fastest to slowest):")
    for strategy, time_ms in write_times:
        print(f"  {strategy}: {time_ms:.2f} ms")
    
if __name__ == "__main__":
    time.sleep(2)
    
    print("Running comparison of all caching strategies...")
    results = compare_all_strategies(reads=10000, writes=10)
    
    plot_hit_rates(results)
    plot_operation_times(results)
    
    print_performance_analysis(results)
    
    print("\nSimulation complete. Check hit_rates.png and operation_times.png for visualizations.")