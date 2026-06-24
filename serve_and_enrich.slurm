#!/bin/bash
#SBATCH --account=def-jic823
#SBATCH --job-name=price-enrich
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=96G
#SBATCH --time=01:30:00
#SBATCH --output=/project/def-jic823/price/logs/enrich-%j.out

# Phase B2: serve Qwen3-30B-A3B-Instruct-2507-FP8 on one H100 and run the
# enrichment client over the segmented items. Mirrors the Infinity FP8
# serve+client pattern (warmup probe before the client connects).
set -uo pipefail
BASE=/project/def-jic823/price
MODEL=/project/def-jic823/models/qwen3-30b-a3b-fp8
ITEMS="${1:-$BASE/items.jsonl}"
OUT="${2:-$BASE/letters_llm.jsonl}"
mkdir -p "$BASE/logs"

module --force purge
module load StdEnv/2023 gcc/12.3 python/3.12 opencv/4.13.0 cuda/12.9
source /project/def-jic823/infinity/venv/bin/activate
# /home is over quota: every cache MUST live on /scratch, or vLLM/triton/inductor
# writes blow up with 'Disk quota exceeded' during engine init.
export HF_HOME=/project/def-jic823/models/hf_cache
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export XDG_CACHE_HOME=/scratch/jic823/.cache/qwen
export VLLM_CACHE_ROOT=$XDG_CACHE_HOME/vllm
export TRITON_CACHE_DIR=$XDG_CACHE_HOME/triton
export TORCHINDUCTOR_CACHE_DIR=$XDG_CACHE_HOME/inductor
export FLASHINFER_WORKSPACE_BASE=$XDG_CACHE_HOME/flashinfer
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1
mkdir -p "$VLLM_CACHE_ROOT" "$TRITON_CACHE_DIR" "$TORCHINDUCTOR_CACHE_DIR" "$FLASHINFER_WORKSPACE_BASE"

nvidia-smi -L
PORT=$(python -c "import socket;s=socket.socket();s.bind((\"127.0.0.1\",0));print(s.getsockname()[1]);s.close()")
SLOG="$BASE/logs/enrich-server-${SLURM_JOB_ID}.log"
echo "[job] serving Qwen3 on $PORT -> $SLOG"

vllm serve "$MODEL" \
  --host 127.0.0.1 --port "$PORT" --served-model-name qwen3 \
  --max-model-len 16384 --gpu-memory-utilization 0.90 \
  --enforce-eager \
  --trust-remote-code > "$SLOG" 2>&1 &
VLLM_PID=$!

READY=0
for i in $(seq 1 300); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 60 -X POST \
    "http://127.0.0.1:${PORT}/v1/chat/completions" -H "Content-Type: application/json" \
    -d "{\"model\":\"qwen3\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1}")
  [ "$code" = "200" ] && { READY=1; echo "[job] warm after ~${i}0s"; break; }
  kill -0 $VLLM_PID 2>/dev/null || { echo "[job] SERVER DIED"; tail -60 "$SLOG"; exit 1; }
  sleep 10
done
[ $READY -eq 1 ] || { echo "[job] server not warm"; tail -80 "$SLOG"; kill $VLLM_PID; exit 1; }

echo "[job] enriching $ITEMS -> $OUT"
python "$BASE/llm_enrich.py" "$ITEMS" "$OUT" \
  --api "http://127.0.0.1:${PORT}/v1" --model qwen3
RC=$?
echo "[job] enrich exit=$RC at $(date); stopping server"
kill $VLLM_PID 2>/dev/null; sleep 3
exit $RC
