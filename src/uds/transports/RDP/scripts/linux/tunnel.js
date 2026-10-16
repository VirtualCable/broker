'use strict';
import { Process, Tasks, Logger, File, Utils} from 'runtime';  

// We receive data in "data" variable, which is an object from json readonly

const errorString = `You need to have xfreerdp or Thincast installed and in path for this to work.
Please, install the proper package for your system.
https://github.com/FreeRDP/FreeRDP|* xfreerdp
https://thincast.com/en/products/client|* Thincast`;

// Try, in order of preference, to find other RDP clients
const executablePath =
    Process.findExecutable('udsrdp') ||
    Process.findExecutable('thincast-remote-desktop-client') ||
    Process.findExecutable('thincast-client') ||
    Process.findExecutable('thincast') ||
    Process.findExecutable('xfreerdp3') ||
    Process.findExecutable('xfreerdp2') ||
    Process.findExecutable('xfreerdp');

if (!executablePath) {
    Logger.error('No RDP client found on system');
    throw new Error('No RDP client found on system');
}

// using Utils.expandVars, expand variables of data.freerdp_params (that is an array of strings)
let parameters = data.freerdp_params.map((param) => Utils.expandVars(param));

// Raises an exception if tunnel cannot be started
const tunnel = await Tasks.startTunnel({
    addr: data.tunnel.host,
    port: data.tunnel.port,
    ticket: data.tunnel.ticket,
    startup_time_ms: data.tunnel.startup_time,
    check_certificate: data.tunnel.verify_ssl,
    shared_secret: data.shared_secret
});

let process = null;

// If has the as_file property, create the temp file on home folder and use it
if (data.as_file) {
    Logger.debug('Has as_file property, creating temp RDP file');
    // Replace "{address}" with data.address in the as_file content
    let content = data.as_file.replace(/\{address\}/g, `127.0.0.1:${tunnel.port}`);
    // Create and save the temp file
    let rdpFilePath = File.createTempFile(File.getHomeDirectory(), content, '.rdp');
    Logger.debug(`RDP temp file created at ${rdpFilePath}`);

    // Append to removable task to delete the file later
    Tasks.addEarlyUnlinkableFile(rdpFilePath);
    let password = data.password ? `/p:${data.password}` : '/p:';
    // Launch the RDP client with the temp file, the addres in INSIDE the file is already set to
    process = Process.launch(executablePath, [password, rdpFilePath]);
} else {
    // Launch the RDP client with the parameters
    process = Process.launch(executablePath, [...parameters, `/v:127.0.0.1:${tunnel.port}`]);
}

Tasks.addWaitableApp(process);