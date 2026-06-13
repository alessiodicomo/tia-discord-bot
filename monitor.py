import requests
import time
import sys

# Constants
STRIDE_API_URL = "https://stride-api.polkachu.com/Stride-Labs/stride/stakeibc/host_zone"

# IBC Denoms
TIA_IBC = "ibc/D79E7D83AB399BFFF93433E54FAA480C191248FC556924A2A8351AE2638B3877"
STTIA_IBC = "ibc/698350B8A61D575025F3ED13E9AC9C0F45C89DEFE92F76D5838F1D3C1A7FF7C9"

def get_stride_redemption_rate():
    """Fetches the Stride Protocol redemption rate for stTIA."""
    try:
        response = requests.get(STRIDE_API_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        for zone in data.get("host_zone", []):
            if zone.get("chain_id") == "celestia":
                rate = float(zone.get("redemption_rate"))
                return rate
        print("Errore: chain_id 'celestia' non trovata nei dati di Stride.")
        return None
    except Exception as e:
        print(f"Errore durante il recupero dei dati da Stride: {e}")
        return None

def get_osmosis_swap_rate(amount_tia=1.0):
    """Fetches the actual output amount of stTIA when swapping `amount_tia` TIA on Osmosis."""
    try:
        # Convert TIA to micro TIA (6 decimals)
        amount_in_micro = int(amount_tia * 1_000_000)
        url = f"https://sqs.osmosis.zone/router/quote?tokenIn={amount_in_micro}{TIA_IBC}&tokenOutDenom={STTIA_IBC}"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # amount_out is returned in micro units (6 decimals)
        amount_out_micro = int(data.get("amount_out", 0))
        # Convert micro stTIA to stTIA
        sttia_received = amount_out_micro / 1_000_000.0
        return sttia_received
    except Exception as e:
        print(f"Errore durante il recupero dei dati da Osmosis per {amount_tia} TIA: {e}")
        return None

def get_tia_usd_price():
    """Fetches the current USD price of TIA from Osmosis SQS."""
    url = f"https://sqs.osmosis.zone/tokens/prices?base={TIA_IBC}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        base_prices = data.get(TIA_IBC, {})
        if not base_prices:
            return None
        
        usd_price_str = list(base_prices.values())[0]
        return float(usd_price_str)
    except Exception as e:
        print(f"Errore durante il recupero del prezzo USD di TIA: {e}")
        return None

def main():
    print("Iniziando il monitoraggio arbitraggio TIA -> stTIA...")
    print("-" * 80)
    
    stride_rate = get_stride_redemption_rate()
    if stride_rate is None:
        sys.exit("Impossibile ottenere il rate di Stride.")
        
    tia_usd_price = get_tia_usd_price()
    if tia_usd_price is None:
        print("Avviso: Impossibile ottenere il prezzo in USD di TIA.")
        tia_usd_price = 0.0
        
    print(f"[Stride]  Protocol redemption rate: 1 stTIA = {stride_rate:.6f} TIA")
    if tia_usd_price > 0:
        print(f"[Osmosis] TIA Price = ${tia_usd_price:.4f}")
    print("-" * 80)
    
    # Testiamo diverse quantità per mostrare lo slippage
    if len(sys.argv) > 1:
        try:
            test_amounts = [float(sys.argv[1])]
        except ValueError:
            print("Importo non valido. Uso importi di default.")
            test_amounts = [1, 10, 100, 1000, 5000, 10000]
    else:
        test_amounts = [1, 10, 100, 1000, 5000, 10000]
    
    print(f"{'TIA in':>10} | {'stTIA out':>12} | {'TIA finali':>12} | {'Profitto %':>10} | {'Profitto USD':>12}")
    print("-" * 80)
    
    for amount in test_amounts:
        sttia_received = get_osmosis_swap_rate(amount)
        if sttia_received is None:
            continue
            
        final_tia_amount = sttia_received * stride_rate
        profit_tia = final_tia_amount - amount
        profit_percentage = (profit_tia / amount) * 100
        profit_usd = profit_tia * tia_usd_price
        
        profit_str = f"+{profit_percentage:.2f}%" if profit_percentage > 0 else f"{profit_percentage:.2f}%"
        profit_usd_str = f"+${profit_usd:.2f}" if profit_usd > 0 else f"-${abs(profit_usd):.2f}"
        if tia_usd_price == 0.0:
            profit_usd_str = "N/A"
            
        print(f"{amount:>10} | {sttia_received:>12.6f} | {final_tia_amount:>12.6f} | {profit_str:>10} | {profit_usd_str:>12}")
        
        # Piccola pausa per non spammare l'API di Osmosis
        time.sleep(0.2)
        
if __name__ == "__main__":
    main()
