"""Generate 9 complex-domain overlapping documents for advanced eval."""
import os

out = os.path.join(os.path.dirname(__file__), "eval_data")
os.makedirs(out, exist_ok=True)

docs = {}

# Domain 1: 3 Payment APIs with overlapping concepts (refund, webhook, order)
docs["api_paygate.txt"] = """# PayGate Payment API v3.2

## POST /orders
amount(cents), currency(ISO 4217), out_trade_no(max 32)
Response: order_id(UUID), status(PENDING/PAID/EXPIRED/CANCELLED), pay_url
Error: ERR_40001 missing param, ERR_40002 bad signature, ERR_40003 amount > 1M CNY
Error: ERR_42901 rate limit 100/min, ERR_50001 internal error

## POST /orders/{id}/refund
refund_amount(cents, <=original), reason(max 200)
Response: refund_id, status(PROCESSING/SUCCESS/FAILED)
Error: ERR_40004 refund over limit, ERR_40005 status not PAID, ERR_40201 balance low

## Webhook
Events: order.paid, refund.success, refund.failed
Signature: X-Sig = HMAC-SHA256(secret, body). Retry: 1m/5m/15m/30m/1h x5
IP whitelist: 203.0.113.0/24, Max: 1,000,000 CNY/transaction"""

docs["api_stripepay.txt"] = """# StripePay API v2.1

## POST /v2/payments
amount(cents), currency(ISO 4217), payment_method(card/alipay/wechat)
Response: payment_id(32-char hex), status(PENDING/CONFIRMED/FAILED/REFUNDED)
Error: PAY_001 missing amount, PAY_002 invalid currency, PAY_003 > 500K CNY limit
Error: PAY_429 rate limit 200/min, PAY_500 gateway error

## POST /v2/refunds
payment_id(required), amount(cents,optional=full), reason(max 500)
Response: refund_id, status(PENDING/SUCCESS/REJECTED)
Error: REF_001 payment not found, REF_002 amount exceeds payment
Error: REF_003 not CONFIRMED, REF_004 merchant balance insufficient

## Webhook
Events: payment.confirmed, payment.failed, refund.completed
Sig: StripePay-Sig = HMAC-SHA256(key, timestamp+body)
Retry: 30s/2m/10m/30m/2h x5. IP: 198.51.100.0/24. Max: 500K CNY"""

docs["api_fastpay.txt"] = """# FastPay Gateway API v1.5

## POST /api/v1/transactions
merchant_id, amount(cents), currency(CNY/USD/EUR), callback_url, ttl(1800s)
Response: txn_id(36-char), qr_code, status(NEW/PAID/EXPIRED/CLOSED)
Error: Txn_1001 param missing, Txn_1002 merchant not found
Error: Txn_1003 <1 CNY or >2M CNY, Txn_1004 duplicate, Txn_5001 system error

## POST /api/v1/refunds
txn_id, amount(cents, <=txn), reason(max 1000), notify_url
Response: refund_txn_id, status(PENDING/SUCCESS/FAILED)
Error: Ref_2001 txn not found, Ref_2002 txn not PAID
Error: Ref_2003 over amount, Ref_2004 credit insufficient

## Callback
Events: transaction.paid, transaction.closed, refund.success, refund.failed
Sig: SHA256-RSA(merchant_private_key, body)
Retry: 1m/5m/15m/1h/4h x5. IP: 192.0.2.0/24, 198.51.100.128/25. Max: 2M CNY"""

# Domain 2: 3 Sensor datasheets with overlapping specs
docs["sensor_sht4x.txt"] = """# SHT4x Digital Humidity/Temperature Sensor (Sensirion)

## SHT40
Humidity: +/-1.8% RH. Temp: +/-0.2C. Range: 0-100% RH, -40C to 125C
Interface: I2C (addr 0x44/0x45), max 1MHz
Supply: 1.08V-3.6V, 0.4uA avg at 1Hz. Package: DFN-4 1.5x1.5mm
Price: CNY 8.50 (1K+). Lead time: in stock

## SHT41
Humidity: +/-1.5% RH. Temp: +/-0.2C. Range: 0-100% RH, -40C to 125C
Interface: I2C (0x44/0x45), max 1MHz. Supply: 1.08V-3.6V, 0.4uA avg
Package: DFN-4 1.5x1.5mm (filter membrane option). Price: CNY 12.80 (1K+)

## SHT45
Humidity: +/-1.0% RH. Temp: +/-0.1C. Range: 0-100% RH, -40C to 125C
Interface: I2C (0x44/0x45), max 1MHz. Supply: 1.08V-3.6V
Package: DFN-4 1.5x1.5mm with PTFE membrane. Price: CNY 22.00 (1K+)
Automotive AEC-Q100 Grade 1 qualified"""

docs["sensor_hdc3x.txt"] = """# HDC3x Digital Humidity Sensor (Texas Instruments)

## HDC3020
Humidity: +/-1.5% RH. Temp: +/-0.1C. Range: 0-100% RH, -40C to 125C
Interface: I2C (addr 0x44/0x45), max 1MHz, alert pin
Supply: 1.62V-3.6V, 0.5uA avg at 1Hz. Package: WSON-8 2.5x2.5mm
Price: CNY 7.20 (1K+), CNY 5.80 (10K+). Lead time: in stock
Integrated heater for condensation removal

## HDC3021
Humidity: +/-1.0% RH. Temp: +/-0.1C. Range: 0-100% RH, -40C to 125C
Interface: I2C (0x44/0x45), max 1MHz, alert pin, NIST traceable
Supply: 1.62V-3.6V, 0.5uA avg. Package: WSON-8 2.5x2.5mm (IP67 option)
Price: CNY 12.50 (1K+), CNY 9.80 (10K+)

## HDC3022
Humidity: +/-0.8% RH. Temp: +/-0.1C. Range: 0-100% RH, -40C to 125C
Interface: I2C (0x44/0x45), max 1MHz, dual alert, NIST certificate
Supply: 1.62V-3.6V, 0.5uA avg. Package: WSON-8 2.5x2.5mm (IP67 cover)
Price: CNY 18.00 (1K+), CNY 14.50 (10K+). For medical/cold chain use"""

docs["sensor_bme280.txt"] = """# BME280 Humidity+Pressure+Temperature Sensor (Bosch)

## BME280 Standard
Humidity: +/-3% RH. Temp: +/-0.5C. Pressure: +/-1.0 hPa
Range: 0-100% RH, -40C to 85C, 300-1100 hPa
Interface: I2C (addr 0x76/0x77), SPI (mode 0/3), max 3.4MHz
Supply: 1.71V-3.6V, 3.6uA avg (weather monitoring mode)
Package: LGA-8 2.5x2.5x0.93mm. Price: CNY 15.00 (1K+), CNY 11.20 (10K+)
Integrated IIR filter for pressure stability

## BME280-H (High Accuracy)
Humidity: +/-2% RH. Temp: +/-0.3C. Pressure: +/-0.6 hPa (pre-screened)
Same interface, package as standard. Price: CNY 22.00 (1K+), CNY 17.50 (10K+)
For weather stations, indoor navigation, altitude tracking"""

# Domain 3: 3 compliance docs with overlapping requirements
docs["compliance_gdpr.txt"] = """# GDPR Compliance (EU 2016/679)

## Lawful Basis (Art.6-9)
Art.6: consent, contract, legal obligation, vital interests, public interest, legitimate interests
Art.7: consent must be freely given, specific, informed, unambiguous; withdrawable anytime
Art.9: sensitive data (racial, political, biometric, health, sexual orientation) requires explicit consent

## Data Subject Rights (Art.15-22)
Art.15: right of access to personal data. Art.17: right to erasure (be forgotten)
Art.20: right to data portability in structured machine-readable format

## Breach Notification (Art.33-34)
Art.33: notify supervisory authority within 72 hours of awareness
Art.34: notify data subjects without undue delay if high risk to rights/freedoms
Fines: up to 20M EUR or 4% of global annual turnover (whichever higher)"""

docs["compliance_pipl.txt"] = """# Personal Information Protection Law (PIPL) - China

## Legal Basis (Art.13-17)
Art.13: consent, contract necessity, statutory duty, public health emergency, news reporting
Art.14: consent must be voluntary, explicit, fully informed; separate consent for sharing,
  sensitive data, cross-border transfer, public disclosure
Art.17: privacy notice required before collection: processor ID, purpose, method, categories,
  retention period, rights exercise method

## Individual Rights (Art.44-48)
Art.44: right to know and decide. Art.47: right to deletion (purpose achieved,
  retention expired, consent withdrawn, unlawful processing)
Art.45: right to request transfer to designated processor

## Breach Response (Art.57-58)
Art.57: immediately notify department and affected individuals upon breach
  Notification: breach nature, consequences, measures taken, risk mitigation
Fines: up to 50M CNY or 5% of prior year revenue; personal liability;
  credit file record; business suspension possible"""

docs["compliance_pci_dss.txt"] = """# PCI DSS v4.0 Compliance Requirements

## Protect Stored Cardholder Data (Req.3)
3.1: limit storage and retention; delete when no longer needed
3.2: never store sensitive auth data (full track, CVV, PIN) after authorization
3.3: mask PAN (first 6/last 4 max); render unreadable via salted hash, truncation,
  tokenization, or strong cryptography
3.4: PAN must be unreadable everywhere stored (DB, logs, backups, archives)

## Encrypt Transmission (Req.4)
4.1: strong cryptography for cardholder data over open networks (TLS 1.2+, IPSEC, SSH)
4.2: wireless must use WPA3 or IEEE 802.11i; never use WEP

## Vulnerability Management (Req.6)
6.1: identify vulnerabilities via reputable external sources; risk ranking (critical/high/medium/low)
6.2: apply vendor security patches within one month of release
6.3: critical patches within 30 days

## Breach Penalties
Non-compliance: USD 5K-100K/month fine; acquiring bank may terminate; forensic audit
Data breach: card brand assessments; mandatory PCI Forensic Investigator (PFI) engagement"""

for filename, content in docs.items():
    path = os.path.join(out, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.strip())
    print(f"Written: {filename} ({len(content)} chars)")

print(f"\nDone: {len(docs)} documents in {out}")
