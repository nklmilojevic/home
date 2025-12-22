{
  description = "homeops development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    talosctl.url = "github:nklmilojevic/talosctl-flake";
  };

  outputs = {
    self,
    nixpkgs,
    flake-utils,
    talosctl,
  }:
    flake-utils.lib.eachDefaultSystem (system: let
      pkgs = import nixpkgs {
        inherit system;
      };
    in {
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
          talhelper
          talosctl.packages.${system}.default
          yq
        ];
      };
    });
}
