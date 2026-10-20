'use strict';
import { Dialog, RDP } from 'runtime';

if (!data.user || !data.password) {
    const credentials = await Dialog.credentials({ user: data.user });
    if (credentials === null) {
        throw new Error('Connection cancelled by user');
    }
    data.user = credentials.user;
    data.password = credentials.password;
}

RDP.start(data);
