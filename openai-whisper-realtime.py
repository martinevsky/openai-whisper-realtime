import sounddevice as sd
import numpy as np

import whisper

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
import uvicorn

import asyncio
import queue
import sys

# SETTINGS
MODEL_TYPE="medium.en"
# the model used for transcription. https://github.com/openai/whisper#available-models-and-languages
LANGUAGE="English"
# pre-set the language to avoid autodetection
BLOCKSIZE=24678 
# this is the base chunk size the audio is split into in samples. blocksize / 16000 = chunk length in seconds. 
SILENCE_THRESHOLD=400
# should be set to the lowest sample amplitude that the speech in the audio material has
SILENCE_RATIO=100
# number of samples in one buffer that are allowed to be higher than threshold

global_ndarray = None
model = whisper.load_model(name=MODEL_TYPE, device="cuda", in_memory=True)

websocket_connections = set()

async def inputstream_generator():
	"""Generator that yields blocks of input data as NumPy arrays."""
	q_in = asyncio.Queue()
	loop = asyncio.get_event_loop()

	def callback(indata, frame_count, time_info, status):
		loop.call_soon_threadsafe(q_in.put_nowait, (indata.copy(), status))

	stream = sd.InputStream(samplerate=16000, channels=1, dtype='int16', blocksize=BLOCKSIZE, callback=callback)
	with stream:
		while True:
			indata, status = await q_in.get()
			yield indata, status		
		
async def process_audio_buffer():
	global global_ndarray
	async for indata, status in inputstream_generator():
		
		indata_flattened = abs(indata.flatten())
				
		# discard buffers that contain mostly silence
		if(np.asarray(np.where(indata_flattened > SILENCE_THRESHOLD)).size < SILENCE_RATIO):
			continue
		
		if (global_ndarray is not None):
			global_ndarray = np.concatenate((global_ndarray, indata), dtype='int16')
		else:
			global_ndarray = indata
			
		# concatenate buffers if the end of the current buffer is not silent
		if (np.average((indata_flattened[-100:-1])) > SILENCE_THRESHOLD/15):
			continue
		else:
			local_ndarray = global_ndarray.copy()
			global_ndarray = None
			indata_transformed = local_ndarray.flatten().astype(np.float32) / 32768.0
			result = model.transcribe(indata_transformed, language=LANGUAGE)
			print(result["text"])
			for connection in websocket_connections:
				await connection.send_text(result["text"])
			
		del local_ndarray
		del indata_flattened

app = FastAPI()

@app.get("/")
def read_root():
    return FileResponse("static/index.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        pass
    finally:
        websocket_connections.remove(websocket)

@app.on_event("startup")
async def startup_event():
    print('\nActivating wire ...\n')
    audio_task = asyncio.create_task(process_audio_buffer())

if __name__ == "__main__":
	uvicorn.run(app, host="localhost", port=8000)
