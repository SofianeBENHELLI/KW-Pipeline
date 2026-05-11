/**
 * Self-contained webpack config for the 3DX Knowledge Explorer widget.
 *
 * Mirrors apps/widget/webpack.config.js so both widgets share the same
 * build invariants (XHTML entry copy, babel-loader for TS/JSX, HTTPS
 * dev server). The dev server runs on port 8082 to avoid colliding
 * with the ingestion widget on 8081 — both can run side-by-side
 * during development.
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
      new CopyPlugin({
        patterns: [
          { from: path.resolve(__dirname, "src/index.html"), to: "index.html" },
        ],
      }),
      // Bake build-time env var into the bundle so the deployed
      // explorer calls the right backend. ``api/client.ts`` reads
      // ``process.env.KW_API_BASE_URL`` at module load — without
      // this plugin the expression stays verbatim in the bundle,
      // ``process`` is undefined in the browser, the lookup throws,
      // the catch returns ``undefined``, and the FALLBACK_BASE_URL
      // (http://localhost:8000) wins. The result is a "deployed"
      // explorer that silently calls localhost from inside
      // 3DDashboard. Empty-string default so dev builds (``npm run
      // build`` / dev server, no env exported) still work — the
      // falsy lookup falls through to the same localhost fallback,
      // matching pre-fix behaviour.
      new webpack.EnvironmentPlugin({
        KW_API_BASE_URL: "",
      }),
    ],
    devServer: {
      server: "https",
      port: 8082,
      open: ["/widget"],
      hot: true,
      static: false,
      historyApiFallback: false,
      devMiddleware: {
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
