'use strict';
import { Process, Tasks, Logger, File } from 'runtime';

const remoteViewerPaths = [
    '/Applications/RemoteViewer.app/Contents/MacOS/RemoteViewer',
    '/opt/homebrew/bin/remote-viewer',
    '/usr/local/bin/remote-viewer',
];

let remoteViewer = null;
for (const path of remoteViewerPaths) {
    if (File.exists(path)) {
        remoteViewer = path;
        break;
    }
}

const errorString = `<p>You need to have installed virt-viewer to connect to this UDS service.</p>
<p>Please, install appropriate package for your system.</p>
<p>You can install it via Homebrew:<br/><code>brew install virt-viewer</code></p>
<p>Or download it from <a href="https://ports.macports.org/port/virt-viewer/">MacPorts</a>.</p>
<p>Please, note that in order to UDS Connector to work correctly, you must copy the Remote Viewer app to your Applications Folder.<br/>
Also remember, that in order to allow this app to run on your system, you must open it one time once it is copied to your App folder</p>`;

if (!remoteViewer) {
    Logger.error('No SPICE client (remote-viewer) found');
    throw new Error(errorString);
}

let theFile = data.as_file_ns;
let fs = null;
let fss = null;

if (data.ticket) {
    fs = await Tasks.startTunnel({
        addr: data.tunHost,
        port: data.tunPort,
        ticket: data.ticket,
        startup_time_ms: data.tunWait,
        check_certificate: data.tunChk,
    });
}

if (data.ticket_secure) {
    theFile = data.as_file;
    fss = await Tasks.startTunnel({
        addr: data.tunHost,
        port: data.tunPort,
        ticket: data.ticket_secure,
        startup_time_ms: data.tunWait,
        check_certificate: data.tunChk,
    });
}

theFile = theFile
    .replace('{secure_port}', fss ? fss.port : '-1')
    .replace('{port}', fs ? fs.port : '-1');

const filename = File.createTempFile(File.getHomeDirectory(), theFile, '.vv');
Tasks.addEarlyUnlinkableFile(filename);
Logger.debug(`Launching SPICE client (${remoteViewer}) with ${filename}`);
const process = Process.launch(remoteViewer, [filename]);
Tasks.addWaitableApp(process);
