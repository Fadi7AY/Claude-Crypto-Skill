"""spotkit - shared library behind the crypto spot trading skills.

Every skill script adds this folder to sys.path via _bootstrap.py, so the
skills stay thin CLIs over one tested code path (analysis, backtest and the
live bot all score signals with the same functions).
"""
__version__ = "1.0.0"
