#!/bin/bash
set -e
echo "=== Instalando Playwright browsers ==="
python -m playwright install chromium --with-deps
echo "=== Browser instalado. Iniciando MercadoBot ==="
python bot.py
