#!/bin/bash
echo "Instalando Playwright browsers..."
python -m playwright install chromium
python -m playwright install-deps chromium
echo "Iniciando MercadoBot..."
python bot.py
