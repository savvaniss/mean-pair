# WebSocket-first exchange migration considerations

This note expands on moving off the current Binance REST-first flow toward a WebSocket-capable provider (e.g., CCXT Pro or an exchange-native socket API). It highlights application-wide impacts, including charting and configuration updates.

## Exchange abstraction and provider swap
- Introduce an exchange service interface (account, symbol metadata, order placement, historical candles, and streaming tickers/order books) to decouple the app from Binance-specific clients and exceptions.
- Implement a WebSocket-enabled adapter (CCXT Pro or a direct exchange SDK) that exposes unified async streams; keep REST fallbacks for authenticated actions when sockets are unavailable.
- Shift environment/config keys to provider-neutral names (API key/secret, environment, default quote/market) so deployments can switch providers without code edits.

## Streaming and polling cadence
- Replace 20s polling loops with socket-driven listeners for prices, depth, and user data; add backpressure/heartbeat handling to reconnect cleanly on disconnects.
- Provide a small in-memory cache (or pub/sub fan-out) so multiple routes and background workers can consume the same live feed without duplicating subscriptions.

## Graphs and UI data flow
- Update charting endpoints or websocket relays to publish live candle/price updates instead of serving stale 20s snapshots.
- Normalize incoming tick data to the chart library’s expected candle schema (open/high/low/close/volume) and compute derived values (e.g., indicators) incrementally to keep graphs smooth.
- When websockets are unavailable, fall back to a shorter REST polling interval and clarify in the UI when data is degraded, so charts reflect the current freshness level.

## Backtesting and history
- Route historical candle requests through the exchange abstraction, allowing a switch between REST history and recorded WebSocket streams.
- Add a retention policy for streamed tick data so chart backfills and engine warm-ups can hydrate from recent socket captures instead of cold REST pulls.

## Configuration changes
- Add toggles for provider choice, WebSocket enablement, reconnect/backoff settings, and maximum stream subscriptions per symbol.
- Expose chart refresh strategy (websocket vs. REST fallback) and cache durations as configurable values to align front-end behavior with backend streaming.

## Operational considerations
- Instrument connection metrics (latency, dropped messages, resubscribe counts) to monitor streaming health alongside trading results.
- Validate order sizing and filters using provider metadata (min notional/lot size) pulled via the abstraction layer to prevent exchange-specific rejections.
