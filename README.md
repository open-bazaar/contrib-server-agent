# bazaar-contrib-server-agent

The agents of a Bazaar Compute server: every agent on the computers it is connected to, its state and today's usage, its workspace and skills, creating and changing agents, and sharing them with roles. It keeps the last list each computer gave, so the agents of a computer that is away still show.

```toml
[[contribs]]
package = "bazaar-contrib-server-agent~=0.3.0rc1"
[[contribs.enabled]]
entry = "server-agent"
```

A contrib puts tabs and activity sections on an agent's page, and reads the agents an account may see, by accepting `AGENTS`.

When the Contrib market is enabled, the channel and runtime choices end with
"Go to market" on both the new-agent wizard and the configuration page. The
link selects that agent's computer and is shown only to accounts with market
view access to it. Empty choices still retain the link.
