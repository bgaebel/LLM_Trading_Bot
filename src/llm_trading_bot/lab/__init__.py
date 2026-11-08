"""The strategy lab (Spec 016, ADR-0026): candle archive, backtests through the unchanged live
code, fidelity check, metrics, trial register and reports.

Runs on the PC only, never on the server. The CLI and the service never import this package at
start (``trading-bot lab ...`` loads it lazily, Spec 016 AK11).
"""
