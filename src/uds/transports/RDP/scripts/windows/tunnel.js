'use strict';
import { Process, Tasks, Logger, File, Utils, RDP } from 'runtime';

// Try, in order of preference, to find other RDP clients
const mstscPath = Process.findExecutable('mstsc.exe', ['C:\\Windows\\System32', 'C:\\Windows\\SysWOW64']);

if (!mstscPath) {
    Logger.error('No RDP client found on system');
    throw new Error('Unable to find mstsc.exe.');
}
let password = '';
try {
    password = Utils.cryptProtectData(data.password);
} catch (e) {
    Logger.info('Could not encrypt password via DPAPI, user will be prompted: ' + e);
}

try {
    Utils.writeHkcuDword('Software\\Microsoft\\Terminal Server Client\\LocalDevices', '127.0.0.1', 255);
} catch (e) {
    Logger.info('Could not write registry key for device redirection: ' + e);
}

const tunnel = await Tasks.startTunnel({
    addr: data.tunnel.host,
    port: data.tunnel.port,
    ticket: data.tunnel.ticket,
    startup_time_ms: data.tunnel.startup_time,
    check_certificate: data.tunnel.verify_ssl,
    shared_secret: data.shared_secret,
    use_udp: true
});

let content = data.as_file.replace(/\{password\}/g, password);
content = content.replace(/\{address\}/g, `127.0.0.1:${tunnel.port}`);

// sign after {address} substitution, otherwise full address won't match
if (data.ticket_sign) {
    content = await RDP.sign(content, data.ticket_sign);
}

let rdpFilePath = File.createTempFile(null, content, '.rdp');
let process = Process.launch(mstscPath, [rdpFilePath]);
Tasks.addEarlyUnlinkableFile(rdpFilePath);
Tasks.addWaitableApp(process);
Logger.info(`Launched RDP client with file ${rdpFilePath}`);
