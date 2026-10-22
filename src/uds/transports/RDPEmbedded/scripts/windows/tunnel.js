'use strict';
import { Dialog, Tasks, Logger, RDP } from 'runtime';

if (!data.user || !data.password) {
    const res = await Dialog.show({
        title: 'Remote Desktop Credentials',
        fields: [
            { name: 'user', label: 'Username', type: 'string', value: data.user || '' },
            { name: 'password', label: 'Password', type: 'password', value: '' },
        ],
        buttons: [
            { name: 'cancel', label: 'Cancel' },
            { name: 'connect', label: 'Connect', primary: true },
        ],
    });
    if (res.pushed !== 'connect') {
        throw new Error('Connection cancelled by user');
    }
    data.user = res.values.user;
    data.password = res.values.password;
}

const tunnel = await Tasks.startTunnel({
    addr: data.tunnel.host,
    port: data.tunnel.port,
    ticket: data.tunnel.ticket,
    startup_time_ms: data.tunnel.startup_time,
    shared_secret: data.shared_secret
});

data.port = tunnel.port;
data.server = '127.0.0.1';

RDP.start(data);