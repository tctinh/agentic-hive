{
  lib,
  stdenvNoCC,
  makeWrapper,
  bash,
  python3,
  coreutils,
  util-linux,
  gnused,
  gawk,
  gnugrep,
  jq,
  ripgrep,
  git,
  tmux,
}:

stdenvNoCC.mkDerivation {
  pname = "agentic-hive";
  version = "0.1.0";

  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../bin
      ../share
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  buildInputs = [
    bash
    python3
  ];

  installPhase = ''
    runHook preInstall
    install -Dm755 -t $out/bin bin/hive bin/hive-hook bin/hive-launch bin/hive-dash bin/hive-web bin/hive-attach bin/hive-statusline bin/hive-member bin/hive-session
    install -Dm644 -t $out/share/agentic-hive share/member-instruction.md share/dashboard.html share/xterm.js share/xterm.css share/hive_members.py share/hive_config.py
    install -Dm644 -t $out/share/agentic-hive share/omp-hive.js
    patchShebangs $out/bin
    for f in $out/bin/*; do
      wrapProgram "$f" \
        --prefix PATH : "$out/bin:${
          lib.makeBinPath [
            python3
            coreutils
            util-linux
            gnused
            gawk
            gnugrep
            jq
            ripgrep
            git
            tmux
          ]
        }" \
        --set HIVE_SHARE "$out/share/agentic-hive"
    done
    runHook postInstall
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    ${bash}/bin/bash ${../test/package.sh} "$out/bin"
    runHook postInstallCheck
  '';

  meta = {
    description = "Agentic Hive Core: a shared Unix habitat for persistent coding agents";
    mainProgram = "hive";
    platforms = lib.platforms.linux;
  };
}
