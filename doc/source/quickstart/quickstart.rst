=========================
OpenUDS – Quick Start
=========================

This document provides a **quick installation guide** for setting up
**OpenUDS Server and Tunnel Server** on Ubuntu systems.

It is intended for **development, lab, or proof-of-concept environments**.

Repository Structure
====================

::

    openuds/
    ├── actor/
    ├── client/
    ├── guacamole-auth-uds/
    ├── server/
    ├── tunnel-server/
    ├── LICENSE
    ├── README.md
    ├── SECURITY.md
    └── VERSION


System Requirements
===================

- Ubuntu 20.04 / 22.04
- Python 3.10 or newer
- Internet access


Install System Dependencies
===========================

Update package lists and install required packages:

::

    sudo apt update
    sudo apt install -y \
      python3-dev \
      python3-pip \
      python3-virtualenv \
      python3.10-venv \
      build-essential \
      libldap2-dev \
      libsasl2-dev \
      libxml2 \
      libxml2-dev \
      libxmlsec1 \
      libxmlsec1-dev \
      libcurl4-openssl-dev \
      libssl-dev \
      libpangocairo-1.0-0 \
      pkg-config \
      git vim curl wget unzip net-tools \
      numpy

Install additional Python packages:

::

    pip3 install python-memcached


Clone OpenUDS Repository
=======================

Clone the main repository:

::

    git clone https://github.com/VirtualCable/openuds.git
    cd openuds

If SSH keys are not available, convert submodules to HTTPS:

::

    sed -i 's|git@github.com:VirtualCable/|https://github.com/VirtualCable/|g' .gitmodules
    git submodule sync --recursive
    git submodule update --init --recursive


OpenUDS Server Setup
===================

Create Python Virtual Environment
---------------------------------

::

    cd server
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt


Generate Crypto Key
-------------------

::

    sudo mkdir -p /usr/share/uds/etc
    sudo openssl genrsa -out /usr/share/uds/etc/uds-crypto.pem 2048
    sudo chmod 640 /usr/share/uds/etc/uds-crypto.pem
    openssl rsa -in /usr/share/uds/etc/uds-crypto.pem -check


Configure Server
----------------

::

    cp server/src/settings.py.sample settings.py

.. note::

   For production environments, database and security settings must be
   reviewed and customized in ``settings.py``.


Initialize Database
-------------------

::

    python manage.py migrate
    python manage.py createcachetable


Start OpenUDS Server
--------------------

::

    python manage.py runserver 0.0.0.0:8000

Access the web interface:

::

    http://<BROKER_HOST>:8000

Example credentials:

::

    Username: root
    Password: udsmam0

Warning

For security reasons, you should change this password immediately after first login.

Register Tunnel with Broker
===========================

From the ``server`` directory, register a tunnel:

::

    python3 samples/reg_tunnel.py \
      --username root \
      --password <ADMIN_PASSWORD> \
      --broker-host <BROKER_HOST>:8000 \
      --tunnel-ip <TUNNEL_PUBLIC_IP> \
      --no-ssl \
      --no-verify

Successful registration output example:

::

    Registering tunnel with broker at http://<BROKER_HOST>:8000/uds/rest/
    Logged in
    Registered with token "<TUNNEL_TOKEN>"


Configure Tunnel Token and Tunnel Server Setup
==============================================

After registering the tunnel with the broker, a **TUNNEL_TOKEN** will be
returned.

This token must be configured in the tunnel server configuration file
before starting the tunnel service.

Edit the tunnel configuration file:

::

    vim tunnel-server/src/udstunnel.conf

Locate the ``uds_token`` parameter and replace its value with the token
returned during tunnel registration "<TUNNEL_TOKEN>".

Example:

::

    # Broker authentication token
    uds_token = <TUNNEL_TOKEN>

Save the file and exit the editor.

Start the tunnel server using the updated configuration:

::

    cd tunnel-server
    python3 src/udstunnel.py -t -c src/udstunnel.conf

If the configuration is correct, the tunnel server will authenticate
successfully with the broker and appear as **connected** in the OpenUDS
web interface.

.. note::

   - The tunnel server will not connect to the broker if ``uds_token``
     is missing or invalid.
   - Do not commit real tokens to the repository.
   - Always use placeholders such as ``<TUNNEL_TOKEN>`` in documentation
     and sample configuration files.


Verify Installation
===================

- Tunnel appears registered in the OpenUDS web interface
- Tunnel server runs without errors
- Providers and services can be created for testing


Security Notes
==============

- **Never commit sensitive data**, including:
  - Real public IP addresses
  - Passwords
  - Tokens or GitHub Personal Access Tokens

- Always use placeholders such as:
  - ``<BROKER_HOST>``
  - ``<TUNNEL_PUBLIC_IP>``
  - ``<ADMIN_PASSWORD>``


Document Scope
==============

This document is intended as a **quick start guide** for developers
and testers.

It is **not** a production deployment guide.
For production usage, refer to official OpenUDS documentation.
