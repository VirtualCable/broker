'use strict';
import { Process, Tasks, Logger, File, Utils} from 'runtime';

// We receive data in "data" variable, which is an object from json readonly

async function fixSizeParameter(params) {
    // fix resolution parameters (as this needs to be a windows, calc the size)
    let width = '1024', height = '768';
    try {
        let out = await Process.launchAndWait('system_profiler', ['SPDisplaysDataType'], 5000);
        let match = out.stdout.match(/: (\d+) x (\d+)/);
        if (match) {
            width = (parseInt(match[1]) - 4).toString();
            height = Math.floor((parseInt(match[2]) * 90) / 100).toString();
        }
    } catch (e) {
        Logger.error('Error getting system profiler data for display resolution, using safe defaults');
    }
    return params.map(p => Utils.expandVars(p).replace('#WIDTH#', width).replace('#HEIGHT#', height));
}

const msrdc_list = [
    '/Applications/Microsoft Remote Desktop.app',
    '/Applications/Microsoft Remote Desktop.localized/Microsoft Remote Desktop.app',
    '/Applications/Windows App.app',
    '/Applications/Windows App.localized/Windows App.app',
];
const thincast_list = [
    '/Applications/ThinCast Remote Desktop Client.app',
    '/Applications/ThinCast Remote Desktop Client.localized/ThinCast Remote Desktop Client.app',
];

const msrd_li = data.allow_msrdc ? `<li>
            <p><b>Microsoft Remote Desktop</b> from App Store</p>
            <p>
                <ul>
                    <li>Install from <a href="https://apps.apple.com/us/app/microsoft-remote-desktop/id1295203466?mt=12">App Store</a></li>
                </ul>
            </p>
        </li>` : '';
const errorString = `xfreerdp${data.allow_msrdc ? ' or Microsoft Remote Desktop' : ''} or thincast client not found
In order to connect to UDS RDP Sessions, you need to have a
* Xfreerdp from homebrew
  https://brew.sh|Install brew
  Install xquartz
    brew install --cask xquartz
  Install freerdp
    brew install freerdp
* ThinCast Remote Desktop Client
https://thincast.com/en/products/client|Download from here
${msrd_li}
`;

// CLI binaries (run directly via exec). findExecutable returns the resolved path or null.
const udsrdpPath = Process.findExecutable('udsrdp');
const xfreeRdpPath = ['xfreerdp', 'xfreerdp3', 'xfreerdp2']
    .map(e => Process.findExecutable(e))
    .find(p => p);
// .app bundles (must be launched through `open -a`). msrdBundle stays null unless explicitly allowed.
const thincastBundle = thincast_list.find(p => File.isDirectory(p));
const msrdBundle = data.allow_msrdc ? msrdc_list.find(p => File.isDirectory(p)) : null;

// Bail out before starting the tunnel if no usable client is present (avoids leaking a tunnel).
if (!udsrdpPath && !xfreeRdpPath && !thincastBundle && !(msrdBundle && data.as_file)) {
    Logger.error('No RDP client found on system');
    throw new Error(errorString);
}

// Raises an exception if tunnel cannot be started
const tunnel = await Tasks.startTunnel({
    addr: data.tunnel.host,
    port: data.tunnel.port,
    ticket: data.tunnel.ticket,
    startup_time_ms: data.tunnel.startup_time,
    check_certificate: data.tunnel.verify_ssl,
    shared_secret: data.shared_secret
});

const tunnelAddress = `127.0.0.1:${tunnel.port}`;

function renderAsFile() {
    const rendered = data.as_file.replace(/\{address\}/g, tunnelAddress);
    const rdpFilePath = File.createTempFile(File.getHomeDirectory(), rendered, 'rdp');
    Tasks.addEarlyUnlinkableFile(rdpFilePath);
    return rdpFilePath;
}

async function launchCli(exe) {
    Logger.info(`Using RDP CLI client at ${exe}`);
    let cliArgs;
    if (data.as_file) {
        cliArgs = [data.password ? `/p:${data.password}` : '/p:', renderAsFile()];
    } else {
        cliArgs = [`/v:${tunnelAddress}`, ...(await fixSizeParameter(data.freerdp_params))];
    }
    Process.launch(exe, cliArgs);
}

async function launchThincast() {
    Logger.info(`Using Thincast at ${thincastBundle}`);
    let openArgs;
    if (data.as_file) {
        openArgs = ['-a', thincastBundle, '--args', data.password ? `/p:${data.password}` : '/p:', renderAsFile()];
    } else {
        const xfparms = await fixSizeParameter(data.freerdp_params);
        openArgs = ['-a', thincastBundle, '--args', `/v:${tunnelAddress}`, ...xfparms];
    }
    Process.launch('/usr/bin/open', openArgs);
}

function launchMsrdc() {
    Logger.info(`Using MSRDC at ${msrdBundle}`);
    // The .rdp must be handed to the app as a document operand (openDocument event), NOT behind
    // `--args`. With `--args` the path lands in the app's argv, which the modern "Windows App"
    // (and Microsoft Remote Desktop) does not parse, yielding "The RDP file is not valid".
    Process.launch('/usr/bin/open', ['-a', msrdBundle, renderAsFile()]);
}

// Preference order (per transport configuration):
//   udsrdp first always.
//   If allow_msrdc is enabled at the transport AND msrdc is available with an as_file, msrdc is second.
//   Then thincast, then xfreerdp.
if (udsrdpPath) {
    await launchCli(udsrdpPath);
} else if (msrdBundle && data.as_file) {
    launchMsrdc();
} else if (thincastBundle) {
    await launchThincast();
} else if (xfreeRdpPath) {
    await launchCli(xfreeRdpPath);
} else {
    // Unreachable: the early-throw above guarantees at least one client is present.
    Logger.error('No RDP client found on system');
    throw new Error(errorString);
}
