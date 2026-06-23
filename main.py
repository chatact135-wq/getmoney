    # --- RESTORED ORIGINAL INDICATORS ---
    ema5_series = df.ta.ema(length=5)
    ema13_series = df.ta.ema(length=13)
    rsi_series = df.ta.rsi(length=14)
    atr_series = df.ta.atr(length=14)
    
    # Re-adding the ADX trend strength filter
    adx_data = df.ta.adx(length=14)

    # (API and DataFrame safety checks go here)

    ema5 = ema5_series.iloc[-2]
    ema13 = ema13_series.iloc[-2]
    rsi = rsi_series.iloc[-2]
    
    # ADX is the first column in the pandas_ta output
    adx = adx_data.iloc[:, 0].iloc[-2] 

    action = "WAIT"
    reason = "No clear 5m momentum"
    
    # --- THE BREAKOUT LOGIC ---
    if ema5 > ema13 and rsi > 55 and close > open_price and adx > [MISSING_VALUE]:
        action = "BUY"
        reason = "Confirmed Bullish Breakout (5m)"
