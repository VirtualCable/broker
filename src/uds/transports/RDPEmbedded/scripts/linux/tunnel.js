'use strict';
import { Dialog, Tasks, Logger, RDP } from 'runtime';

if (!data.user || !data.password) {
    const credentials = await Dialog.credentials({ user: data.user });
    if (credentials === null) {
        throw new Error('Connection cancelled by user');
    }
    data.user = credentials.user;
    data.password = credentials.password;
}

const tunnel = await Tasks.startTunnel({
    addr: data.tunnel.host,
    port: data.tunnel.port,
    ticket: data.tunnel.ticket,
    startup_time_ms: data.tunnel.startup_time,
    shared_secret: data.shared_secret,
});

data.port = tunnel.port;
data.server = '127.0.0.1';

RDP.start(data);