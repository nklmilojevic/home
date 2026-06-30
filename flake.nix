{
  description = "homeops development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    talosctl.url = "github:nklmilojevic/talosctl-flake";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      talosctl,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            cloudflared
            fluxcd
            gum
            helmfile
            just
            kubeconform
            kubectl
            kubernetes-helm
            kustomize
            lefthook
            minijinja
            moreutils
            yamlfmt
            yamllint
            stern
            talosctl.packages.${system}.default
            vals
            yq-go
            oxfmt
            zizmor
          ];
        };
      }
    );
}
