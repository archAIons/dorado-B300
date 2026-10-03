'use strict';
const $ = id => document.getElementById(id), API = '/basecalling-public-api';
const state = {
    connected: false,
    busy: false,
    watching: false,
    stop: false,
    files: [],
    max: 2 * 1024 ** 3,
    chunk: 8 * 1024 ** 2,
    gpus: [],
    lastHealth: 0
};
const delay = ms => new Promise(r => setTimeout(r, ms));
const size = n =>
        n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(2) + ' GiB' : (n / 1024 ** 2).toFixed(1) + ' MiB';
function message(text = '') {
    $('message').textContent = text;
    $('message').hidden = !text;
}
function controls() {
    const available = state.gpus.some(
            g => g.id === $('gpu').value && g.available &&
                    (!g.models || g.models.includes($('model').value)));
    $('uploadButton').disabled =
            !state.connected || state.busy || !state.files.length || !available;
    $('watchButton').disabled = !state.connected ||
            (!state.watching && (state.busy || !available)) || !window.showDirectoryPicker;
    for (const field of document.querySelectorAll('.run-options'))
        field.disabled = state.busy;
    updateDemux();
    $('watchButton').textContent =
            state.watching ? 'Stop watching folder' : 'Connect sequencing folder';
    for (const id
                 of ['runName', 'kit', 'model', 'gpu', 'files', 'batchTab', 'liveTab', 'trim', 'qc',
                     'minQscore', 'minLength'])
        $(id).disabled = state.busy;
}
function updateGpu() {
    const selected = state.gpus.find(g => g.id === $('gpu').value);
    $('gpuHint').textContent = selected?.detail || 'Connect to check GPU availability.';
    if (selected?.available && selected.models && !selected.models.includes($('model').value))
        $('gpuHint').textContent = `This GPU currently supports ${
                selected.models.map(m => m.toUpperCase())
                        .join(' / ')}. Choose a supported model or another GPU.`;
    $('gpuSummary').textContent = selected ? selected.name +
                    (selected.available ? ' · ' +
                                     (selected.id === 'H100' ? 'Dorado 2.1.1' :
                                                               'Experimental build') :
                                          ' · Preparing') :
                                             'Connect to choose a GPU';
    controls();
}
function applyHealth(health) {
    state.gpus = health.gpus || [];
    state.lastHealth = Date.now();
    state.catalog = health;
    if (!state.kitsLoaded) {
        const kit = $('kit').value;
        $('kit').replaceChildren(
                ...Object.entries(health.kits || {})
                        .map(([value, label]) => new Option(value + ' · ' + label, value)));
        $('kit').value = kit;
        state.kitsLoaded = true;
        renderModifications()
    }
    for (const option of $('gpu').options) {
        const gpu = state.gpus.find(g => g.id === option.value);
        if (gpu)
            option.textContent = gpu.name + ' · ' + (gpu.available ? 'Ready' : 'Preparing')
    }
    $('serviceStatus').textContent = health.worker_online ? 'Server ready' : 'Worker is starting';
    $('statusDot').classList.toggle('ready', health.worker_online);
    updateGpu();
}
$('gpu').value = localStorage.getItem('basecall-public-gpu') || 'B300';
if (!$('gpu').value)
    $('gpu').value = 'B300';
$('gpu').addEventListener('change', () => {
    localStorage.setItem('basecall-public-gpu', $('gpu').value);
    updateGpu();
    message()
});
$('model').addEventListener('change', () => {
    renderModifications();
    updateGpu()
});
async function api(path, options = {}) {
    const response =
            await fetch(API + path, {credentials: 'same-origin', cache: 'no-store', ...options});
    if (!response.ok) {
        let error;
        try {
            error = await response.json()
        } catch {
            error = { detail: 'Request failed (' + response.status + ')' }
        }
        if (response.status === 401) {
            state.connected = false;
            $('accessPanel').hidden = false;
            controls()
        }
        throw new Error(
                typeof error.detail === 'string' ? error.detail :
                                                   'Request failed (' + response.status + ')');
    }
    return response.json();
}
async function connect(key) {
    const health = await api('/health');
    state.connected = true;
    state.max = health.max_file_bytes;
    state.chunk = health.chunk_bytes;
    $('accessPanel').hidden = true;
    applyHealth(health);
    await refresh();
}
$('runName').value = localStorage.getItem('basecall-public-run') ||
        'run-' + new Date().toISOString().slice(0, 10) + '-' + crypto.randomUUID().slice(0, 5);
$('runName').addEventListener('change', () => {
    localStorage.setItem('basecall-public-run', $('runName').value.trim());
    refresh()
});
localStorage.setItem('basecall-public-run', $('runName').value);
for (const [id, panel, other, otherPanel] of [
             ['batchTab', 'batchPanel', 'liveTab', 'livePanel'],
             ['liveTab', 'livePanel', 'batchTab', 'batchPanel']]) {
    $(id).addEventListener('click', () => {
        $(id).setAttribute('aria-selected', 'true');
        $(other).setAttribute('aria-selected', 'false');
        $(panel).hidden = false;
        $(otherPanel).hidden = true;
        message()
    });
}
function select(files) {
    state.files = Array.from(files).filter(f => f.name.toLowerCase().endsWith('.pod5'));
    $('selection').textContent = state.files.length ?
            state.files.map(f => f.name + ' (' + size(f.size) + ')').join(' · ') :
            'No POD5 files selected';
    controls()
}
$('files').addEventListener('change', e => select(e.target.files));
for (const event of ['dragenter', 'dragover'])
    $('dropzone').addEventListener(event, e => {
        e.preventDefault();
        if (!state.busy)
            $('dropzone').classList.add('dragging')
    });
for (const event of ['dragleave', 'drop'])
    $('dropzone').addEventListener(event, e => {
        e.preventDefault();
        $('dropzone').classList.remove('dragging')
    });
$('dropzone').addEventListener('drop', e => {
    if (!state.busy)
        select(e.dataTransfer.files)
});
function progress(label, percent, detail) {
    $('transfer').hidden = false;
    $('transferLabel').textContent = label;
    $('progress').value = percent;
    $('transferDetail').textContent = detail
}
function digest(file) {
    return new Promise((resolve, reject) => {
        const worker = new Worker('./hash-worker.js');
        worker.onmessage = ({data}) => {
            if (state.stop) {
                worker.terminate();
                reject(new Error('Uploads stopped. Choose the same files to resume.'));
                return
            }
            if (data.error) {
                worker.terminate();
                reject(new Error(data.error))
            } else if (data.digest) {
                worker.terminate();
                resolve(data.digest)
            } else
                progress(
                        'Checking ' + file.name, data.progress,
                        'Calculating a checksum without loading the full file into memory.');
        };
        worker.onerror = () => {
            worker.terminate();
            reject(new Error('Could not calculate the file checksum. Please reload and try again.'))
        };
        worker.postMessage(file);
    })
}
async function upload(file, run, model, gpu) {
    if (!file.size || file.size > state.max)
        throw new Error(file.name + ': choose a nonempty file up to ' + size(state.max) + '.');
    const sha256 = await digest(file);
    let job = await api('/uploads', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            run,
            filename: file.name,
            sha256,
            size: file.size,
            model,
            gpu,
            options: runOptions()
        })
    });
    if (job.state !== 'uploading')
        return job;
    let failures = 0;
    while (job.offset < file.size) {
        if (state.stop)
            throw new Error('Uploads stopped. Choose the same files and run name to resume.');
        progress(
                'Uploading ' + file.name, job.offset / file.size * 100,
                size(job.offset) + ' / ' + size(file.size));
        try {
            job = await api('/uploads/' + job.id + '?offset=' + job.offset, {
                method: 'PUT',
                headers: {'Content-Type': 'application/octet-stream'},
                body: file.slice(job.offset, job.offset + state.chunk)
            });
            failures = 0;
        } catch (error) {
            if (++failures > 3)
                throw error;
            await delay(failures * 1500);
            job = await api('/jobs/' + job.id);
        }
    }
    progress(
            'Validating ' + file.name, 100,
            'Checking POD5 integrity before submitting to the GPU.');
    job = await api('/uploads/' + job.id + '/complete', {method: 'POST'});
    if (job.state === 'uploading')
        throw new Error(job.error || 'Upload needs to be retried');
    await refresh();
    return job;
}
$('uploadButton').addEventListener('click', async () => {
    if (!validOptions())
        return;
    const run = $('runName').value.trim();
    if (!run) {
        message('Enter a run name.');
        return
    }
    state.busy = true;
    state.stop = false;
    controls();
    message();
    let submitted = 0, errors = [];
    const files = [...state.files];
    for (const file of files) {
        if (state.stop)
            break;
        try {
            const job = await upload(file, run, $('model').value, $('gpu').value);
            if (job.state === 'failed')
                errors.push(file.name + ': ' + job.error);
            else
                submitted++
        } catch (err) {
            errors.push(err.message)
        }
    }
    state.busy = false;
    controls();
    $('transfer').hidden = true;
    message(errors.length ? errors.join('\n') :
                            submitted + ' file(s) submitted. Basecalling continues on the server.');
    await refresh();
});
$('pauseButton').addEventListener('click', () => {
    state.stop = true;
    state.watching = false;
    controls()
});
async function* walk(handle, prefix = '') {
    for await (const [name, child] of handle.entries()) {
        if (child.kind === 'directory')
            yield* walk(child, prefix + name + '/');
        else if (name.toLowerCase().endsWith('.pod5'))
            yield [prefix + name, child]
    }
}
$('watchButton').addEventListener('click', async () => {
    if (state.watching) {
        state.watching = false;
        state.stop = true;
        controls();
        return
    }
    let folder;
    try {
        folder = await window.showDirectoryPicker({mode: 'read'})
    } catch (err) {
        if (err.name !== 'AbortError')
            message(err.message);
        return
    }
    if (!validOptions())
        return;
    const run = $('runName').value.trim();
    if (!run) {
        message('Enter a run name.');
        return
    }
    state.watching = true;
    state.busy = true;
    state.stop = false;
    controls();
    message('Watching ' + folder.name + '. Keep this tab open.');
    const seen = new Map(), sent = new Map();
    try {
        while (state.watching) {
            for await (const [name, handle] of walk(folder)) {
                if (!state.watching)
                    break;
                const file = await handle.getFile(), stamp = file.size + ':' + file.lastModified;
                if (sent.get(name) === stamp)
                    continue;
                if (seen.get(name)?.stamp !== stamp) {
                    seen.set(name, {stamp, time: Date.now()});
                    continue
                }
                if (Date.now() - seen.get(name).time < 30000)
                    continue;
                try {
                    const job = await upload(file, run, $('model').value, $('gpu').value);
                    sent.set(name, stamp);
                    if (job.state === 'failed')
                        message(name + ': ' + job.error)
                } catch (err) {
                    message(name + ': ' + err.message + ' The next scan will retry.');
                    if (!state.connected) {
                        state.watching = false;
                        break
                    }
                }
            }
            if (state.watching) {
                progress(
                        'Watching ' + folder.name, 0,
                        'Waiting for new, completed POD5 files. ' + sent.size +
                                ' file(s) submitted.');
                await delay(5000)
            }
        }
    } catch (err) {
        message('Folder watching stopped: ' + err.message)
    } finally {
        state.watching = false;
        state.busy = false;
        controls();
        $('transfer').hidden = true
    }
});
if (!window.showDirectoryPicker)
    $('watchSupport').textContent =
            'Live folder access needs desktop Chrome or Edge. File upload works in this browser.';
function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls)
        node.className = cls;
    if (text !== undefined)
        node.textContent = text;
    return node
}
async function refresh() {
    if (!state.connected)
        return;
    try {
        if (Date.now() - state.lastHealth > 15000)
            applyHealth(await api('/health'));
        const jobs = await api('/jobs?run=' + encodeURIComponent($('runName').value.trim()));
        $('filesCount').textContent = jobs.length;
        $('completeCount').textContent = jobs.filter(j => j.state === 'complete').length;
        const reads = jobs.reduce((n, j) => n + (j.metrics?.reads || 0), 0);
        $('readsCount').textContent = reads ? reads.toLocaleString() : '—';
        $('emptyState').hidden = jobs.length > 0;
        const list = $('jobList');
        list.replaceChildren();
        for (const job of jobs) {
            const row = el('article', 'job'), head = el('div', 'job-head');
            head.append(
                    el('div', 'job-name', job.filename),
                    el('span', 'state ' + job.state,
                       job.state === 'running' ? 'Basecalling' : job.state));
            row.append(head);
            row.append(el(
                    'div', 'job-meta',
                    size(job.size) + ' · ' + job.model.toUpperCase() + ' · ' + (job.gpu || 'B300') +
                            (job.metrics?.reads ?
                                     ' · ' + job.metrics.reads.toLocaleString() + ' reads' :
                                     '') +
                            (job.metrics?.compute_seconds ?
                                     ' · ' + job.metrics.compute_seconds + ' s' :
                                     '')));
            if (job.options)
                row.append(el(
                        'div', 'job-meta',
                        job.options.kit +
                                (job.options.modifications.length ?
                                         ' · ' + job.options.modifications.join(' + ') :
                                         '') +
                                (job.options.demultiplex ? ' · Demultiplex' : '') + ' · Q ≥ ' +
                                job.options.min_qscore + ' · Length ≥ ' + job.options.min_length));
            if (job.state === 'uploading') {
                const p = el('progress');
                p.max = job.size;
                p.value = job.offset;
                p.setAttribute('aria-label', 'Upload progress');
                row.append(p)
            }
            if (job.error)
                row.append(el('div', 'job-error', job.error));
            if (job.state === 'complete') {
                const links = el('div', 'downloads');
                for (const [kind, title] of [
                             ['bam', 'Download BAM'], ['fastq.gz', 'Download FASTQ'],
                             ['provenance.json', 'Run details'],
                             ...(job.options?.qc ? [['qc.json', 'QC report']] : []),
                             ...(job.options?.demultiplex ?
                                         [['demultiplexed.zip', 'Barcode BAMs (ZIP)']] :
                                         [])]) {
                    const a = el('a', '', title);
                    a.href = API + '/jobs/' + job.id + '/download/' + kind;
                    links.append(a)
                }
                row.append(links)
            }
            list.append(row);
        }
    } catch (err) {
        message(err.message)
    }
}
$('refreshButton').addEventListener('click', refresh);
setInterval(refresh, 5000);

connect().catch(err => {
    if (!err.message.includes('Sign in'))
        message(err.message)
});

function updateDemux() {
    const kit = $('kit').value, supported = (state.catalog?.barcode_kits || []).includes(kit);
    $('demultiplex').disabled = state.busy || !supported;
    if (!supported)
        $('demultiplex').checked = false;
    const both = supported && kit.includes('NBD') && $('demultiplex').checked;
    $('barcodeBothEnds').disabled = state.busy || !both;
    if (!both)
        $('barcodeBothEnds').checked = false;
    $('demuxHint').textContent = supported ?
            'Download one BAM per barcode, including unclassified reads, as a ZIP.' :
            'Choose a barcoding kit to enable demultiplexing.';
}
function renderModifications() {
    const previous = Array.from(document.querySelectorAll('[data-mod-base]')).map(n => n.value);
    const rna = $('kit').value.startsWith('SQK-RNA');
    const mods = rna ? state.catalog?.rna_modifications?.[$('model').value] :
                       state.catalog?.dna_modifications;
    const groups = {};
    for (const [value, title] of Object.entries(mods || {})) {
        const base = value === '6mA' || value === 'm6A_DRACH' || value.startsWith('inosine') ? 'A' :
                value.startsWith('pseU')                                                     ? 'U' :
                value === '2OmeG'                                                            ? 'G' :
                                                                                               'C';
        (groups[base] ||= []).push([value, title]);
    }
    $('modifications').replaceChildren();
    for (const [base, options] of Object.entries(groups)) {
        const label =
                el('label', '', ({A: 'Adenine', C: 'Cytosine', G: 'Guanine', U: 'Uracil'})[base]);
        const select = document.createElement('select');
        select.dataset.modBase = base;
        select.append(new Option('No modification calling', ''));
        for (const [value, title] of options)
            select.append(new Option(title, value));
        const selected = options.find(([value]) => previous.includes(value));
        if (selected)
            select.value = selected[0];
        label.append(select);
        $('modifications').append(label);
    }
    updateDemux();
}
function runOptions() {
    return {
        kit: $('kit').value,
                modifications: Array.from(document.querySelectorAll('[data-mod-base]'))
                        .map(n => n.value)
                        .filter(Boolean),
                demultiplex: $('demultiplex').checked,
                barcode_both_ends: $('barcodeBothEnds').checked, trim: $('trim').checked,
                qc: $('qc').checked, min_qscore: Number($('minQscore').value),
                min_length: Number($('minLength').value)
    }
}
function validOptions() {
    for (const id of ['minQscore', 'minLength'])
        if (!$(id).reportValidity())
            return false;
    return true
}
$('kit').addEventListener('change', renderModifications);
$('demultiplex').addEventListener('change', updateDemux);

state.catalog = RUN_CATALOG;
$('kit').replaceChildren(
        ...Object.entries(RUN_CATALOG.kits)
                .map(([value, label]) => new Option(value + ' · ' + label, value)));
state.kitsLoaded = true;
renderModifications();
