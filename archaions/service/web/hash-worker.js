importScripts('./sha256.min.js');
self.onmessage = async ({data: file}) => {
    try {
        const hash = sha256.create(), step = 4 * 1024 * 1024;
        for (let offset = 0; offset < file.size; offset += step) {
            hash.update(await file.slice(offset, offset + step).arrayBuffer());
            self.postMessage({progress: Math.min(100, (offset + step) / file.size * 100)});
        }
        self.postMessage({digest: hash.hex()});
    } catch (error) {
        self.postMessage({error: error.message});
    }
};
