'use strict';
import { Process, Tasks, Logger, File } from 'runtime';

// We receive data in "data" variable, which is an object from json readonly

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

const spiceFilePath = File.createTempFile(File.getHomeDirectory(), data.as_file, '.vv');
Logger.debug(`SPICE temp file created at ${spiceFilePath}`);

Tasks.addEarlyUnlinkableFile(spiceFilePath);

Logger.debug(`Launching SPICE client (${remoteViewer}) with ${spiceFilePath}`);
const process = Process.launch(remoteViewer, [spiceFilePath]);
Tasks.addWaitableApp(process);
