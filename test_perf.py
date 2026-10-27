import asyncio
import time

class MockWS:
    def __init__(self, fail=False):
        self.fail = fail

    async def send_json(self, msg):
        await asyncio.sleep(0.01) # Simulate network IO
        if self.fail:
            raise Exception("Fail")

async def test_sequential(num_ws):
    websockets = {MockWS(i % 10 == 0) for i in range(num_ws)}
    message = {"data": "test"}
    start = time.time()

    dead = set()
    for ws in list(websockets):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)

    end = time.time()
    return end - start

async def test_concurrent(num_ws):
    websockets = {MockWS(i % 10 == 0) for i in range(num_ws)}
    message = {"data": "test"}
    start = time.time()

    ws_list = list(websockets)

    async def _send_to_ws(ws):
        try:
            await ws.send_json(message)
            return None
        except Exception:
            return ws

    results = await asyncio.gather(*(_send_to_ws(ws) for ws in ws_list))
    dead = {ws for ws in results if ws is not None}

    end = time.time()
    return end - start

async def main():
    print("Sequential (100 WS):", await test_sequential(100))
    print("Concurrent (100 WS):", await test_concurrent(100))

if __name__ == "__main__":
    asyncio.run(main())
