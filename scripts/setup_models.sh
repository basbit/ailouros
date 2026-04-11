#!/usr/bin/env bash
# setup_models.sh — auto-detect hardware and pull the best Ollama models
set -euo pipefail

BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; RESET='\033[0m'

echo -e "${BOLD}AIlourOS — Model Setup${RESET}"
echo "Detecting your hardware..."

# ── Detect RAM ────────────────────────────────────────────────────────────────
detect_ram_gb() {
  if [[ "$OSTYPE" == darwin* ]]; then
    sysctl -n hw.memsize | awk '{printf "%d", $1/1024/1024/1024}'
  elif [[ -f /proc/meminfo ]]; then
    awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo
  else
    echo "8"  # safe default
  fi
}

# ── Detect GPU (Apple Silicon / NVIDIA) ──────────────────────────────────────
detect_gpu() {
  if [[ "$OSTYPE" == darwin* ]]; then
    CHIP=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "")
    if [[ "$CHIP" == *"Apple"* ]]; then
      echo "apple_silicon"
      return
    fi
  fi
  if command -v nvidia-smi &>/dev/null; then
    VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
    echo "nvidia:${VRAM}"
    return
  fi
  echo "cpu"
}

RAM_GB=$(detect_ram_gb)
GPU=$(detect_gpu)

echo -e "  RAM:  ${CYAN}${RAM_GB} GB${RESET}"
echo -e "  GPU:  ${CYAN}${GPU}${RESET}"
echo ""

# ── Model selection ───────────────────────────────────────────────────────────
# Priority: coding-focused, OpenAI-compatible, works well with Ollama
select_models() {
  local ram=$1
  local gpu=$2

  # Apple Silicon uses unified memory — RAM = effective VRAM
  if [[ "$gpu" == "apple_silicon" ]]; then
    if   (( ram >= 64 )); then echo "qwen2.5-coder:32b deepseek-r1:32b"
    elif (( ram >= 32 )); then echo "qwen2.5-coder:14b qwen2.5-coder:32b"
    elif (( ram >= 16 )); then echo "qwen2.5-coder:14b"
    elif (( ram >= 8  )); then echo "qwen2.5-coder:7b"
    else                       echo "qwen2.5-coder:3b"
    fi
    return
  fi

  # NVIDIA — check VRAM
  if [[ "$gpu" == nvidia:* ]]; then
    local vram=${gpu#nvidia:}
    if   (( vram >= 24000 )); then echo "qwen2.5-coder:14b deepseek-r1:14b"
    elif (( vram >= 12000 )); then echo "qwen2.5-coder:7b"
    elif (( vram >= 6000  )); then echo "qwen2.5-coder:3b"
    else                           echo "qwen2.5-coder:1.5b"
    fi
    return
  fi

  # CPU-only — based on RAM
  if   (( ram >= 32 )); then echo "qwen2.5-coder:14b"
  elif (( ram >= 16 )); then echo "qwen2.5-coder:7b"
  elif (( ram >= 8  )); then echo "qwen2.5-coder:3b"
  else                       echo "qwen2.5-coder:1.5b"
  fi
}

MODELS=$(select_models "$RAM_GB" "$GPU")
PRIMARY=$(echo "$MODELS" | awk '{print $1}')

echo -e "${BOLD}Recommended models for your machine:${RESET}"
for m in $MODELS; do echo -e "  • ${GREEN}$m${RESET}"; done
echo ""

# ── Check Ollama ──────────────────────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
  echo -e "${YELLOW}Ollama not found.${RESET}"
  echo ""
  echo "Install Ollama first:"
  if [[ "$OSTYPE" == darwin* ]]; then
    echo "  brew install ollama"
    echo "  — or download from https://ollama.com/download"
  else
    echo "  curl -fsSL https://ollama.com/install.sh | sh"
  fi
  echo ""
  echo -e "${YELLOW}Alternatively use LM Studio:${RESET} https://lmstudio.ai"
  echo "  → Download the model manually in the app"
  echo "  → Enable Local Server (OpenAI-compatible)"
  echo ""
  echo "After installing Ollama, re-run: make models"
  exit 0
fi

# ── Start Ollama if not running ───────────────────────────────────────────────
if ! ollama list &>/dev/null 2>&1; then
  echo "Starting Ollama..."
  ollama serve &>/dev/null &
  sleep 3
fi

# ── Pull models ───────────────────────────────────────────────────────────────
echo -e "${BOLD}Pulling models via Ollama...${RESET}"
for MODEL in $MODELS; do
  echo -e "\n${CYAN}→ ollama pull $MODEL${RESET}"
  ollama pull "$MODEL" || echo -e "${YELLOW}  Warning: failed to pull $MODEL, skipping${RESET}"
done

# ── Write .env if not exists ──────────────────────────────────────────────────
ENV_FILE="$(dirname "$0")/../.env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$(dirname "$0")/../.env.example" ]]; then
    cp "$(dirname "$0")/../.env.example" "$ENV_FILE"
    echo ""
    echo -e "${GREEN}Created .env from .env.example${RESET}"
  fi
fi

# Patch primary model into .env
if [[ -f "$ENV_FILE" ]]; then
  if grep -q "^SWARM_MODEL=" "$ENV_FILE"; then
    sed -i.bak "s|^SWARM_MODEL=.*|SWARM_MODEL=$PRIMARY|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    echo "SWARM_MODEL=$PRIMARY" >> "$ENV_FILE"
  fi
  echo -e "${GREEN}Set SWARM_MODEL=$PRIMARY in .env${RESET}"
fi

echo ""
echo -e "${BOLD}${GREEN}Done!${RESET} Models ready. Now run:"
echo -e "  ${CYAN}make start${RESET}   — start AIlourOS"
echo ""
