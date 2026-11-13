'use strict';
import { Dialog, URP } from 'runtime';

if (!data.has_credentials) {
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

URP.start(data);
