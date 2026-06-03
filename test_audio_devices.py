import pyaudio
pa = pyaudio.PyAudio()
print(f"Device count: {pa.get_device_count()}")
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    print(f"Device {i}: {info['name']} (in={info['maxInputChannels']}, out={info['maxOutputChannels']}, rate={info['defaultSampleRate']})")
pa.terminate()
