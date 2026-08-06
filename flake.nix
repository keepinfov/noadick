{
  description = "noadick development shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      devShells = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [
              python313
              uv
              ruff
              pyright
              sqlite
            ];
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
            UV_PROJECT_ENVIRONMENT = ".venv";
            shellHook = ''
              echo "noadick: run 'uv sync' once, then 'uv run pytest'"
            '';
          };
        });
    };
}
