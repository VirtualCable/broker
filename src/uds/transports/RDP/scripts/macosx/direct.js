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

const msrdExecutable = data.allow_msrdc ? msrdc_list.find(p => File.isDirectory(p)) : null;
const udsrdpExecutable = Process.findExecutable('udsrdp') ? 'udsrdp' : null;
const xfreeRdpExecutable = ['xfreerdp', 'xfreerdp3', 'xfreerdp2'].find(e => Process.findExecutable(e));
const thincastExecutable = thincast_list.find(p => File.isDirectory(p));
const executablePath = udsrdpExecutable || thincastExecutable || xfreeRdpExecutable;

let params = [];
// First preference is udsrdp, then thincast, then freerdp and then msrdc (if allowed)
if (executablePath) {
    Logger.info(`Using RDP client at ${executablePath}`);
    if (data.as_file) {
        let rdpFilePath = File.createTempFile(File.getHomeDirectory(), data.as_file, 'rdp');
        Tasks.addEarlyUnlinkableFile(rdpFilePath);
        params = [executablePath, '--args', data.password ? `/p:${data.password}` : '/p:', rdpFilePath];
    } else {
        params = [executablePath, `/v:${data.address}`, ...(await fixSizeParameter(data.freerdp_params))];
    }
} else if (msrdExecutable) {
    let rdpFilePath = File.createTempFile(File.getHomeDirectory(), data.as_file, 'rdp');
    Tasks.addEarlyUnlinkableFile(rdpFilePath);
    // Open the .rdp as a file (open -a APP FILE); behind --args the app can't parse it.
    params = ['-a', msrdExecutable, rdpFilePath];
} else {
    Logger.error('No RDP client found on system');
    throw new Error(errorString);
}

// On MacOS, we do not need to wait for the app to end, just launch it
Process.launch('/usr/bin/open', params);
