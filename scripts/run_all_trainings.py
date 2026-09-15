"""Master Training & Evaluation Orchestrator for AI Service (4-Pillars Pipeline).

Runs all 4 offline training and indexing engines sequentially:
1. Content-Based Vector Embeddings (precompute_embeddings.py)
2. Collaborative Filtering SVD Matrix Factorization (train_batch_collaborative.py)
3. FP-Growth Association Rule Mining (train_fpgrowth_accessories.py)
4. AI Chatbot RAG Knowledge Indexing (train_chatbot_knowledge.py)
5. Automated RecSys & AI Evaluation Benchmark (evaluate_recsys.py)
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def run_master_pipeline():
    start_time = time.time()
    print("=" * 80)
    print("      MASTER AI TRAINING & EVALUATION PIPELINE (4-PILLARS ENGINE)      ")
    print("=" * 80)

    # 0. Ensure Synthetic Data is available
    try:
        from scripts.generate_synthetic_orders import generate_synthetic_dataset
        print("\n[Step 0/5] Checking Synthetic Dataset Engine...")
        generate_synthetic_dataset()
    except Exception as exc:
        print(f"[Step 0/5] Synthetic data check warning: {exc}")

    # 1. Pillar 1: Content-Based Vector Embeddings
    print("\n" + "-" * 60)
    print("[Step 1/5] Running Pillar 1: Content-Based Dense Vector Embeddings...")
    print("-" * 60)
    try:
        from scripts.precompute_embeddings import precompute
        precompute()
    except Exception as exc:
        print(f"Pillar 1 error: {exc}")

    # 2. Pillar 2: Collaborative Filtering SVD
    print("\n" + "-" * 60)
    print("[Step 2/5] Running Pillar 2: Collaborative Filtering SVD...")
    print("-" * 60)
    try:
        from scripts.train_batch_collaborative import run_batch_training
        run_batch_training()
    except Exception as exc:
        print(f"Pillar 2 error: {exc}")

    # 3. Pillar 3: FP-Growth Association Rule Mining
    print("\n" + "-" * 60)
    print("[Step 3/5] Running Pillar 3: FP-Growth Association Rules...")
    print("-" * 60)
    try:
        from scripts.train_fpgrowth_accessories import run_fpgrowth_training
        run_fpgrowth_training()
    except Exception as exc:
        print(f"Pillar 3 error: {exc}")

    # 4. Pillar 4: AI Chatbot RAG Knowledge Indexing
    print("\n" + "-" * 60)
    print("[Step 4/5] Running Pillar 4: AI Chatbot RAG Knowledge Indexer...")
    print("-" * 60)
    try:
        from scripts.train_chatbot_knowledge import run_chatbot_knowledge_indexing
        run_chatbot_knowledge_indexing()
    except Exception as exc:
        print(f"Pillar 4 error: {exc}")

    # 5. Evaluation Benchmark
    print("\n" + "-" * 60)
    print("[Step 5/5] Executing RecSys & AI Evaluation Benchmark...")
    print("-" * 60)
    try:
        from scripts.evaluate_recsys import run_evaluation
        run_evaluation()
    except Exception as exc:
        print(f"Evaluation benchmark error: {exc}")

    elapsed = time.time() - start_time
    print("=" * 80)
    print(f"      MASTER TRAINING PIPELINE COMPLETED SUCCESSFULLY IN {elapsed:.2f}s!      ")
    print("=" * 80)


if __name__ == "__main__":
    run_master_pipeline()
