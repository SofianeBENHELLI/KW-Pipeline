/**
 * Self-contained webpack config for the 3DX KnowledgeForge widget.
 *
 * The official `@widget-lab/widget-templates-webpack-configs` package
 * provides shared `dev` / `devS3` / `prod` configs but lives on the
 * private 3DS GitLab npm registry — see `.npmrc` for context. This
 * config bakes in the same essentials (XHTML entry copy, babel-loader
 * for TS/JSX, HTTPS dev server on 8081 with the `/widget` path) so
 * the widget can be built on any machine without registry access.
 */
const path = require("path");
const webpack = require("webpack");
const CopyPlugin = require("copy-webpack-plugin");

module.exports = (_env, argv) => {
  const isProd = argv.mode === "production";

  return {
    entry: path.resolve(__dirname, "src/index.tsx"),
    output: {
      path: path.resolve(__dirname, "dist"),
      filename: "main.js",
      clean: true,
    },
    devtool: isProd ? false : "source-map",
    resolve: {
      extensions: [".tsx", ".ts", ".jsx", ".js"],
      // Force module lookups to start from this app's ``node_modules``
      // and fall back to the standard ``node_modules`` resolution.
      // ``apps/_shared/`` has no node_modules of its own (#83 slice 3
      // pulled React-using code in there), so without this webpack
      // can't resolve ``react/jsx-runtime`` / ``@widget-lab/*`` when
      // bundling files from the shared package.
      modules: [path.resolve(__dirname, "node_modules"), "node_modules"],
    },
    module: {
      rules: [
        {
          test: /\.css$/,
          use: ["style-loader", "css-loader"],
        },
        {
          test: /\.(ts|tsx|js|jsx)$/,
          // Transpile our source AND the local 3ddashboard-utils dep
          // (it's already compiled, but babel handles ESM ↔ CJS interop).
          exclude: /node_modules\/(?!@widget-lab\/)/,
          use: {
            loader: "babel-loader",
            options: {
              presets: [
                ["@babel/preset-env", { targets: { esmodules: true } }],
                ["@babel/preset-react", { runtime: "automatic" }],
                "@babel/preset-typescript",
              ],
            },
          },
        },
      ],
    },
    plugins: [
      // The XHTML entry is hand-written and must NOT have webpack inject
      // a <script> tag — its own bootstrap derives `main.js` from
      // `widget.uwaUrl`. We copy it verbatim.
      new CopyPlugin({
        patterns: [
          { from: path.resolve(__dirname, "src/index.html"), to: "index.html" },
        ],
      }),
      // Bake build-time env vars into the bundle so the deployed
      // widget calls the right backend. ``api/client.ts`` reads
      // ``process.env.KW_API_BASE_URL`` / ``process.env.KW_ORBITAL_URL``
      // at module load — without this plugin those expressions stay
      // verbatim in the bundle, ``process`` is undefined in the
      // browser, the lookup throws, the catch returns ``undefined``,
      // and the FALLBACK_BASE_URL (http://localhost:8000) wins. The
      // result is a "deployed" widget that silently calls localhost
      // from inside 3DDashboard. Empty-string defaults so dev builds
      // (``npm run build`` / dev server, no env exported) still work
      // — the falsy lookup falls through to the same localhost
      // fallback, matching pre-fix behaviour.
      new webpack.EnvironmentPlugin({
        KW_API_BASE_URL: "",
        KW_ORBITAL_URL: "",
      }),
    ],
    devServer: {
      server: "https",
      port: 8081,
      open: ["/widget"],
      hot: true,
      static: false,
      historyApiFallback: false,
      devMiddleware: {
        // Serve the bundle under /widget/ so it matches the URL the
        // 3DDashboard host uses for registered tiles.
        publicPath: "/widget/",
        writeToDisk: false,
      },
      client: {
        overlay: { runtimeErrors: false },
      },
    },
    performance: {
      hints: false,
    },
  };
};
