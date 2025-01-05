{
  description = "homeops development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable-small";
    flake-utils.url = "github:numtide/flake-utils";
    talhelper = {
      url = "github:budimanjojo/talhelper";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, talhelper }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            age
            cloudflared
            fluxcd
            go-task
            helmfile
            kubeconform
            kubectl
            kubernetes-helm
            kustomize
            minijinja
            moreutils
            pre-commit
            sops
            stern
            talosctl
            yq
          ] ++ [
            talhelper.packages.${system}.default
          ];
        };
      });
}
