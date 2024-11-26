{
  description = "homeops packaged using poetry2nix";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable-small";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    talhelper = {
      url = "github:budimanjojo/talhelper";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, poetry2nix, talhelper }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
        };
        inherit (poetry2nix.lib.mkPoetry2Nix { inherit pkgs; }) mkPoetryApplication;
      in
      {
        packages = {
          home = mkPoetryApplication {
            projectDir = self;
            preferWheels = true;
            python = pkgs.python312;
          };
          default = self.packages.${system}.home;
        };

        devShells.default = pkgs.mkShell {
          inputsFrom = [ self.packages.${system}.home ];
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

        devShells.poetry = pkgs.mkShell {
          packages = [ pkgs.python312 pkgs.poetry ];
        };
      });
}
