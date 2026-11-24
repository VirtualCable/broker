This is the broker part (Server) of OpenUDS.

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/VirtualCable/openuds)

## Root (superuser) password

There is **no default superuser password**. On the first start against a fresh
database, a random unrecoverable password is generated and stored (hashed), so
nobody can log in as `root` until you set your own:

```bash
python3 manage.py config Security.rootPass=<new password>
```

The value is stored hashed and is not displayed back afterwards. If you lose
it, simply set a new one with the same command.

