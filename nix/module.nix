# Agentic Hive — NixOS module (the "tree", SPEC §4).
#
# Declares the host physics only: the `hive` user, /srv/hive ownership,
# baseline tools, Hive Core, and the Claude Code adapter hooks. Room, claims,
# members and knowledge are mutable habitat state and never live here.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.agentic-hive;
  hookCmd = event: "${cfg.package}/bin/hive-hook claude ${event}";
  hook = event: [ { hooks = [ { type = "command"; command = hookCmd event; } ]; } ];
in
{
  options.services.agentic-hive = {
    enable = lib.mkEnableOption "Agentic Hive habitat";

    beekeeper = lib.mkOption {
      type = lib.types.str;
      description = "Human owner account; added to the hive group for read/write habitat access.";
    };

    root = lib.mkOption {
      type = lib.types.path;
      default = "/srv/hive";
      description = "Mutable habitat root.";
    };

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      description = "Hive Core package (protected in the Nix store).";
    };

    harnesses = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = with pkgs; [
        claude-code
        codex
      ];
      description = "Agent harnesses available to members.";
    };

    extraPackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = with pkgs; [
        godot
        unzip
      ];
      description = "Host-wide project prerequisites (SPEC §5: Godot and common tools).";
    };

    web = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Serve the Beekeeper dashboard and terminal (hive-web).";
      };
      port = lib.mkOption {
        type = lib.types.port;
        default = 80;
        description = "HTTP port for the dashboard.";
      };
      openFirewallOn = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ "tailscale0" ];
        description = "Interfaces on which the dashboard port is opened. Empty = localhost only in practice.";
      };
    };

    claudeHooks = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Install the Hive adapter as Claude Code managed-settings hooks
        (/etc/claude-code/managed-settings.json). The hooks are no-ops for any
        session without HIVE_MEMBER set, including the Beekeeper's own.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    users.groups.hive = { };

    users.users.hive = {
      isNormalUser = true;
      group = "hive";
      home = "/home/hive";
      createHome = true;
      # The Beekeeper is in the hive group and may inspect the home directory.
      # Agent-managed credentials and session folders keep their own modes.
      homeMode = "750";
      description = "Agentic Hive members";
      # No wheel, no password: the Beekeeper enters via sudo or SSH keys.
    };

    users.users.${cfg.beekeeper}.extraGroups = [ "hive" ];

    # Let the Beekeeper launch/attach member sessions without a root shell.
    security.sudo.extraRules = [
      {
        users = [ cfg.beekeeper ];
        runAs = "hive";
        commands = [
          {
            command = "ALL";
            options = [ "NOPASSWD" ];
          }
        ];
      }
    ];

    # Setgid + default ACL: everything created in the habitat stays
    # group-writable for hive and the Beekeeper regardless of umask.
    systemd.tmpfiles.rules = [
      "d ${cfg.root} 2770 hive hive -"
      "a+ ${cfg.root} - - - - default:group:hive:rwX,group:hive:rwX,default:user:hive:rwX"
    ];

    systemd.services.agentic-hive-init = {
      description = "Initialize Agentic Hive habitat (idempotent)";
      wantedBy = [ "multi-user.target" ];
      after = [ "systemd-tmpfiles-setup.service" ];
      serviceConfig = {
        Type = "oneshot";
        User = "hive";
        Group = "hive";
        UMask = "0002";
        Environment = "HIVE_ROOT=${cfg.root}";
        ExecStart = "${cfg.package}/bin/hive init";
      };
    };

    # Beekeeper dashboard and shell, running as the non-root hive user.
    systemd.services.hive-web = lib.mkIf cfg.web.enable {
      description = "Agentic Hive Beekeeper dashboard";
      wantedBy = [ "multi-user.target" ];
      after = [
        "network.target"
        "agentic-hive-init.service"
      ];
      # Web controls launch member harnesses and a host shell through this
      # service. Their binaries must be in its PATH, not only the login profile.
      path = [ cfg.package pkgs.bash pkgs.git pkgs.tmux ] ++ cfg.harnesses;
      environment = {
        HIVE_ROOT = cfg.root;
        HIVE_BIN_DIR = "/run/current-system/sw/bin";
        # git refuses repos owned by another user unless marked safe.
        GIT_CONFIG_COUNT = "1";
        GIT_CONFIG_KEY_0 = "safe.directory";
        GIT_CONFIG_VALUE_0 = "*";
      };
      serviceConfig = {
        ExecStart = "${cfg.package}/bin/hive-web --address 0.0.0.0 --port ${toString cfg.web.port}";
        User = "hive";
        Group = "hive";
        AmbientCapabilities = [ "CAP_NET_BIND_SERVICE" ];
        CapabilityBoundingSet = [ "CAP_NET_BIND_SERVICE" ];
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        # The browser host shell needs the hive account's own home for history
        # and normal CLI configuration; Unix permissions still separate users.
        ProtectHome = false;
        # CLI delivery/probes and harnesses use mktemp. Keep /tmp shared so
        # dashboard clients can reach the existing tmux server sockets there.
        ReadWritePaths = [ cfg.root "/home/hive" "/tmp" ];
        PrivateTmp = false;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
        RestrictNamespaces = true;
        LockPersonality = true;
        # Dashboard-launched Codex tools initialize V8 and need executable
        # memory; denying it crashes codex-code-mode-host with SIGTRAP.
        MemoryDenyWriteExecute = false;
        SystemCallArchitectures = "native";
        Restart = "on-failure";
        RestartSec = 2;
      };
    };

    networking.firewall.interfaces = lib.mkIf cfg.web.enable (
      lib.genAttrs cfg.web.openFirewallOn (_: {
        allowedTCPPorts = [ cfg.web.port ];
      })
    );

    environment.etc."claude-code/managed-settings.json" = lib.mkIf cfg.claudeHooks {
      text = builtins.toJSON {
        # Members' status line: Room generation and account quota; also records
        # the quota for the dashboard. Prints just the model name elsewhere.
        statusLine = {
          type = "command";
          command = "${cfg.package}/bin/hive-statusline";
          padding = 0;
        };
        hooks = {
          SessionStart = hook "SessionStart";
          UserPromptSubmit = hook "UserPromptSubmit";
          PostToolUse = [
            {
              matcher = "*";
              hooks = [ { type = "command"; command = hookCmd "PostToolUse"; } ];
            }
          ];
          PostToolUseFailure = [
            {
              matcher = "*";
              hooks = [ { type = "command"; command = hookCmd "PostToolUseFailure"; } ];
            }
          ];
          Stop = hook "Stop";
          StopFailure = hook "StopFailure";
          Notification = [
            {
              matcher = "idle_prompt";
              hooks = [ { type = "command"; command = hookCmd "Notification"; } ];
            }
          ];
          SessionEnd = hook "SessionEnd";
        };
      };
    };

    environment.variables = {
      HIVE_ROOT = cfg.root;
      # Member sessions pin a package path for their whole lifetime; the system
      # profile tracks rebuilds, so hive-hook/hive-launch use it to spot a moved
      # CLI and keep Codex's hook path stable.
      HIVE_BIN_DIR = "/run/current-system/sw/bin";
    };

    # Member sessions are tmux; make attaching and copying painless:
    # mouse selection, OSC 52 clipboard (works over SSH), big scrollback.
    programs.tmux = {
      enable = true;
      historyLimit = 100000;
      terminal = "tmux-256color";
      extraConfig = ''
        set -g mouse on
        set -g set-clipboard on
        set -as terminal-features ',*:clipboard'
        set -g allow-passthrough on
        set -g focus-events on
        set -sg escape-time 10
        # Keep the selection after a mouse drag instead of jumping to the bottom.
        unbind -T copy-mode MouseDragEnd1Pane
        unbind -T copy-mode-vi MouseDragEnd1Pane
        bind -T copy-mode MouseDragEnd1Pane send -X copy-selection-no-clear
        bind -T copy-mode-vi MouseDragEnd1Pane send -X copy-selection-no-clear
        set -g status-right ' #S · %H:%M '
      '';
    };

    programs.direnv = {
      enable = true;
      nix-direnv.enable = true;
    };

    environment.systemPackages =
      [ cfg.package ]
      ++ cfg.harnesses
      ++ (with pkgs; [
        tmux
        git
        git-lfs
        ripgrep
        fd
        fzf
        tree
        jq
        curl
        wget
        rsync
        lsof
        strace
        procps
        psmisc
        iproute2
        btop
        ncdu
        inotify-tools
        acl
        bubblewrap
        xvfb
        python3
      ])
      ++ cfg.extraPackages;
  };
}
